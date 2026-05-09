"""Tool broker: intercept, validate, and route tool calls.

Routes tool calls through trust zone enforcement,
parameter validation, and audit logging before execution.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import ValidationError as PydanticError

from backend.tools.rate_limits import PersistentRateLimiter, ToolRateLimiter


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.tools.registry import ToolDefinition, ToolRegistry
    from backend.trust.zones import TrustZone


logger = logging.getLogger(__name__)


class ToolBrokerError(Exception):
    """Raised when a tool call is rejected."""


@dataclass(frozen=True)
class ToolResult:
    """Result of a tool call.

    Attributes:
        tool_name: Name of the tool.
        success: Whether the call succeeded.
        result: Result data or error message.
        zone: Trust zone of the tool.
        duration_ms: Execution time in milliseconds.
    """

    tool_name: str
    success: bool
    result: object
    zone: TrustZone
    duration_ms: float


@dataclass
class ToolBroker:
    """Intercepts and routes tool calls.

    Enforces trust zones, validates parameters,
    checks rate limits, and delegates execution.

    Attributes:
        registry: Tool registry for lookups.
        rate_limiter: Per-loop rate limiter (reset each agentic loop).
        persistent_limiter: Sliding-window limiter (survives across loops).
    """

    registry: ToolRegistry
    rate_limiter: ToolRateLimiter = field(default_factory=ToolRateLimiter)
    persistent_limiter: PersistentRateLimiter = field(
        default_factory=PersistentRateLimiter
    )
    audit_fn: Callable | None = None
    _handlers: dict[str, Callable] = field(default_factory=dict)
    _audit_tasks: set[asyncio.Task[object]] = field(default_factory=set)
    # Per-call execution context (e.g. conversation_id, session_id).
    # Set by ToolExecutor before broker.execute(), cleared after.
    # Handlers that need it read self.broker.current_context inside their
    # closure.
    current_context: dict[str, str] = field(default_factory=dict)

    def set_context(self, context: dict[str, str]) -> None:
        """Set the per-call execution context."""
        self.current_context = dict(context)

    def clear_context(self) -> None:
        """Clear the per-call execution context."""
        self.current_context = {}

    def _log_audit(
        self,
        event_type: str,
        tool_name: str,
        parameters: object = None,
        result: object = None,
        zone: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Write an audit log entry when an audit callback is available.

        Redacts credentials from parameters and truncates
        the result to 200 characters before logging.

        Args:
            event_type: Audit event type.
            tool_name: Name of the tool.
            parameters: Raw parameters (will be redacted).
            result: Execution result (will be truncated).
            zone: Trust zone value string.
            duration_ms: Execution time in milliseconds.
        """
        from backend.security.content_filter import (
            filter_output,
        )

        redacted_params = None
        if parameters is not None:
            import json

            raw = json.dumps(parameters)
            redacted_params = filter_output(raw).text

        result_summary = None
        if result is not None:
            result_summary = filter_output(str(result)).text[:200]

        if self.audit_fn is None:
            return

        audit_task = asyncio.ensure_future(
            self.audit_fn(
                event_type,
                tool_name,
                parameters=redacted_params,
                result=result_summary,
                zone=zone,
                duration_ms=duration_ms,
            )
        )
        self._audit_tasks.add(audit_task)
        audit_task.add_done_callback(self._audit_tasks.discard)

    def set_handler(self, tool_name: str, handler: Callable) -> None:
        """Register an execution handler for a tool.

        Args:
            tool_name: Tool name.
            handler: Callable to execute the tool.
        """
        self._handlers[tool_name] = handler

    def _tool_definition(self, tool_name: str) -> ToolDefinition:
        """Return one registered tool definition or raise a broker error."""
        tool_def = self.registry.get(tool_name)
        if tool_def is None:
            raise ToolBrokerError(f"Unknown tool: {tool_name}")
        return tool_def

    def _ensure_rate_limits(self, tool_name: str) -> None:
        """Reject one tool call when any rate limit is exceeded."""
        if not self.rate_limiter.is_allowed(tool_name):
            raise ToolBrokerError(f"Rate limit exceeded for {tool_name}")
        if not self.persistent_limiter.is_allowed(tool_name):
            raise ToolBrokerError(f"Rate limit exceeded for {tool_name}")

    def _handler(self, tool_name: str) -> Callable:
        """Return one registered execution handler or raise a broker error."""
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ToolBrokerError(f"No handler for tool: {tool_name}")
        return handler

    def _record_tool_usage(self, tool_name: str) -> None:
        """Record one successful rate-limit hit for the selected tool."""
        self.rate_limiter.record(tool_name)
        self.persistent_limiter.record(tool_name)

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        """Return elapsed milliseconds since one monotonic timestamp."""
        return (time.monotonic() - start) * 1000

    def list_schemas(self) -> list[dict]:
        """List all tool schemas for LLM context.

        Returns:
            List of tool schema dicts.
        """
        return self.registry.list_schemas()

    def list_openai_schemas(
        self,
        allowed_names: set[str] | None = None,
    ) -> list[dict]:
        """List all tool schemas in OpenAI function calling format.

        Returns:
            List of OpenAI-format tool dicts.
        """
        return self.registry.list_openai_schemas(allowed_names=allowed_names)

    async def execute(
        self,
        tool_name: str,
        parameters: dict,
    ) -> ToolResult:
        """Execute a tool call with full validation.

        Args:
            tool_name: Name of the tool to call.
            parameters: Raw parameters from LLM.

        Returns:
            ToolResult with execution outcome.

        Raises:
            ToolBrokerError: If tool unknown or validation
                fails.
        """
        start = time.monotonic()

        try:
            tool_def = self._tool_definition(tool_name)
            zone = tool_def.zone

            self._ensure_rate_limits(tool_name)

            try:
                validated = tool_def.input_model(**parameters)
            except PydanticError as e:
                raise ToolBrokerError(f"Validation failed: {e}") from e

            handler = self._handler(tool_name)

            self._record_tool_usage(tool_name)
            result = await handler(validated)
            elapsed = self._elapsed_ms(start)

            self._log_audit(
                "tool_success",
                tool_name,
                parameters,
                str(result),
                zone.value,
                elapsed,
            )

            return ToolResult(
                tool_name=tool_name,
                success=True,
                result=result,
                zone=zone,
                duration_ms=elapsed,
            )

        except ToolBrokerError as exc:
            elapsed = self._elapsed_ms(start)
            self._log_audit(
                "tool_rejected",
                tool_name,
                parameters,
                str(exc),
                duration_ms=elapsed,
            )
            raise

    def reset_rate_limit(self, tool_name: str) -> None:
        """Reset the rate limit for a specific tool.

        Args:
            tool_name: Tool whose limit to reset.
        """
        self.rate_limiter.reset_tool(tool_name)
