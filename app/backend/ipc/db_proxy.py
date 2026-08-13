"""Generic database proxy dispatcher with operation whitelisting.

Provides OperationRegistry for registering named operations with handler
callables, and ChannelAllowlist for restricting which operations are
available on each IPC socket channel. Used to build a DB Proxy process
that exposes only predefined operations, preventing raw SQL execution
from untrusted processes.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from backend.ipc.models import DBOperation, DBResult


logger = logging.getLogger(__name__)

# --- Constants ---
HEALTH_CHECK_OPERATION = "_health_check"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


class OperationNotFoundError(Exception):
    """Raised when an unknown operation is requested."""


class OperationNotAllowedError(Exception):
    """Raised when an operation is not allowed on the current channel."""


OperationHandler = Callable[
    [dict[str, Any] | None],
    Coroutine[Any, Any, dict[str, Any]],
]

SerializedResult = TypeVar("SerializedResult")


class TransactionResource(Protocol):
    """Minimal transaction surface owned by the DB Proxy registry."""

    @property
    def in_transaction(self) -> bool:
        """Return whether the resource has an open transaction."""

    def rollback(self) -> None:
        """Roll back the current transaction."""


@dataclass
class OperationStats:
    """Runtime statistics for the operation registry.

    Attributes:
        total_operations: Total number of operations executed.
        failed_operations: Total number of failed operations.
        rejected_operations: Total number of rejected (unknown/disallowed) operations.
    """

    total_operations: int = 0
    failed_operations: int = 0
    rejected_operations: int = 0


class OperationRegistry:
    """Registry of named database operations with handler callables.

    Maps operation names to async handler functions. Rejects unknown
    operations with a clear error. Tracks execution statistics.

    Example:
        registry = OperationRegistry()

        async def handle_save_message(params):
            # Insert into database
            return {"message_id": "m1"}

        registry.register("save_message", handle_save_message)
        result = await registry.dispatch(
            DBOperation(request_id="1", operation="save_message", parameters={...})
        )
    """

    def __init__(
        self,
        transaction_resources: Iterable[TransactionResource] = (),
    ) -> None:
        """Initialize an empty operation registry."""
        self._handlers: dict[str, OperationHandler] = {}
        self.stats = OperationStats()
        # IPC sockets dispatch concurrently while handlers share long-lived
        # SQLite connections. Keep each operation, including its transaction,
        # indivisible on those connections.
        self._dispatch_lock = asyncio.Lock()
        self._transaction_resources = tuple(
            {id(resource): resource for resource in transaction_resources}.values()
        )

    def _rollback_open_transactions(self) -> None:
        """Restore every owned connection to a clean transaction boundary."""
        for resource in self._transaction_resources:
            if not resource.in_transaction:
                continue
            try:
                resource.rollback()
            except Exception:
                logger.exception("Failed to roll back a DB Proxy transaction")

    def _ensure_transactions_closed(self) -> None:
        """Reject a handler that returned while retaining an open transaction."""
        if not any(resource.in_transaction for resource in self._transaction_resources):
            return
        self._rollback_open_transactions()
        raise RuntimeError("DB Proxy operation left a transaction open")

    async def run_serialized(
        self,
        callback: Callable[[], SerializedResult],
    ) -> SerializedResult:
        """Run synchronous maintenance under the operation transaction guard."""
        async with self._dispatch_lock:
            try:
                result = callback()
                self._ensure_transactions_closed()
                return result
            except Exception:
                self._rollback_open_transactions()
                raise

    @staticmethod
    def _error_result(request_id: str, message: str) -> DBResult:
        """Build one error DB result."""
        return DBResult(
            request_id=request_id,
            status=STATUS_ERROR,
            error_message=message,
        )

    @staticmethod
    def _success_result(request_id: str, data: dict[str, Any]) -> DBResult:
        """Build one success DB result."""
        return DBResult(
            request_id=request_id,
            status=STATUS_SUCCESS,
            data=data,
        )

    @staticmethod
    def _unknown_operation_message(name: str) -> str:
        """Build the standard error text for one unknown operation."""
        return f"Unknown operation: '{name}'"

    def _rejected_unknown_operation(self, operation: DBOperation) -> DBResult:
        """Build the standard DB result for one unknown operation request."""
        self.stats.rejected_operations += 1
        logger.warning("Unknown operation: %s", operation.operation)
        return self._error_result(
            operation.request_id,
            self._unknown_operation_message(operation.operation),
        )

    def register(self, name: str, handler: OperationHandler) -> None:
        """Register a named operation with its handler.

        Args:
            name: Operation name (e.g., "save_message").
            handler: Async callable(parameters) -> dict result.
        """
        self._handlers[name] = handler
        logger.debug("Registered operation: %s", name)

    def has_operation(self, name: str) -> bool:
        """Check if an operation is registered.

        Args:
            name: Operation name.

        Returns:
            True if the operation is registered.
        """
        return name in self._handlers

    def list_operations(self) -> list[str]:
        """Return list of all registered operation names."""
        return list(self._handlers.keys())

    async def dispatch(self, operation: DBOperation) -> DBResult:
        """Execute a named operation and return the result.

        Args:
            operation: DBOperation with operation name and parameters.

        Returns:
            DBResult with success or error status.
        """
        handler = self._handlers.get(operation.operation)
        if handler is None:
            return self._rejected_unknown_operation(operation)

        self.stats.total_operations += 1
        async with self._dispatch_lock:
            try:
                result_data = await handler(operation.parameters)
                self._ensure_transactions_closed()
                return self._success_result(operation.request_id, result_data)
            except Exception as e:
                self._rollback_open_transactions()
                self.stats.failed_operations += 1
                logger.error(
                    "Operation '%s' failed: %s",
                    operation.operation,
                    e,
                )
                return self._error_result(operation.request_id, str(e))


class ChannelAllowlist:
    """Restricts which operations are allowed per IPC channel.

    Each channel has an explicit set of allowed operation names.
    Operations not in the allowlist are rejected with a clear error.

    Example:
        allowlist = ChannelAllowlist()
        allowlist.set_allowed("socket_a", {"save_message", "load_context"})
        allowlist.set_allowed("socket_b", {"get_credential", "store_credential"})

        allowlist.check("socket_a", "save_message")  # OK
        allowlist.check(
            "socket_a",
            "get_credential",
        )  # Raises OperationNotAllowedError
    """

    def __init__(self) -> None:
        """Initialize with no channels configured."""
        self._channels: dict[str, set[str]] = {}

    def _allowed_operations(self, channel: str) -> set[str]:
        """Return the stored allowlist for one channel."""
        return self._channels.get(channel, set())

    def set_allowed(self, channel: str, operations: set[str]) -> None:
        """Define the allowed operations for a channel.

        Args:
            channel: Channel identifier (e.g., "socket_a", "socket_b").
            operations: Set of operation names allowed on this channel.
        """
        self._channels[channel] = set(operations)
        logger.debug(
            "Channel '%s' allowlist set: %d operations",
            channel,
            len(operations),
        )

    def is_allowed(self, channel: str, operation: str) -> bool:
        """Check if an operation is allowed on a channel.

        Args:
            channel: Channel identifier.
            operation: Operation name to check.

        Returns:
            True if allowed, False otherwise.
        """
        return operation in self._allowed_operations(channel)

    def check(self, channel: str, operation: str) -> None:
        """Check if an operation is allowed, raising on denial.

        Args:
            channel: Channel identifier.
            operation: Operation name to check.

        Raises:
            OperationNotAllowedError: If operation is not allowed on this channel.
        """
        if not self.is_allowed(channel, operation):
            raise OperationNotAllowedError(
                f"Operation '{operation}' not allowed on channel '{channel}'",
            )

    def get_allowed(self, channel: str) -> set[str]:
        """Return the set of allowed operations for a channel.

        Args:
            channel: Channel identifier.

        Returns:
            Set of allowed operation names (empty if channel not configured).
        """
        return self._allowed_operations(channel)


@dataclass
class HealthStatus:
    """Health check response for the DB Proxy.

    Attributes:
        status: Health status ("healthy" or "unhealthy").
        db_connected: Whether the database connection is active.
        operations_registered: Number of registered operations.
        total_operations_executed: Total operations dispatched.
    """

    status: str
    db_connected: bool
    operations_registered: int
    total_operations_executed: int


def create_health_handler(
    registry: OperationRegistry,
    db_check: Callable[[], Coroutine[Any, Any, bool]],
) -> OperationHandler:
    """Create a health check operation handler.

    Args:
        registry: The operation registry to report stats from.
        db_check: Async callable that returns True if DB is connected.

    Returns:
        Async handler function for the health check operation.
    """

    def _health_payload(db_ok: bool) -> dict[str, Any]:
        """Build the current DB proxy health payload."""
        status = HealthStatus(
            status="healthy" if db_ok else "unhealthy",
            db_connected=db_ok,
            operations_registered=len(registry.list_operations()),
            total_operations_executed=registry.stats.total_operations,
        )
        return {
            "status": status.status,
            "db_connected": status.db_connected,
            "operations_registered": status.operations_registered,
            "total_operations_executed": status.total_operations_executed,
        }

    async def _health_check(
        parameters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return current health status."""
        db_ok = await db_check()
        return _health_payload(db_ok)

    return _health_check
