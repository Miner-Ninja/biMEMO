"""Worker proxy handler factory.

Creates async handlers that route tool calls to FS Worker
or Net Worker via its dispatch handle.
"""

import asyncio
import logging
import uuid
from typing import Protocol, TYPE_CHECKING

from backend.ipc.protocol import ToolRequest, ToolResponse


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class WorkerHandle(Protocol):
    """Minimal worker dispatch interface used by tool handlers.

    Implemented by the orchestrator's in-process worker adapter
    (processes/orchestrator/tool_setup).
    """

    async def send_request(self, request: ToolRequest) -> ToolResponse:
        """Send one tool request and return the worker response."""


logger = logging.getLogger(__name__)

TOOL_TIMEOUTS: dict[str, float] = {
    "file_read": 10.0,
    "file_write": 30.0,
    "file_search": 30.0,
    "web_search": 15.0,
    "http_fetch": 45.0,
    "email_send": 15.0,
}
DEFAULT_TOOL_TIMEOUT = 30.0


class WorkerProxyError(Exception):
    """Raised when a worker proxy call fails."""


def _request_id() -> str:
    """Return a new worker request identifier."""
    return str(uuid.uuid4())


def _tool_timeout(tool_name: str) -> float:
    """Return the configured timeout for one worker-routed tool."""
    return TOOL_TIMEOUTS.get(tool_name, DEFAULT_TOOL_TIMEOUT)


def _tool_request(
    tool_name: str,
    parameters: dict,
) -> ToolRequest:
    """Build one worker IPC request."""
    return ToolRequest(
        request_id=_request_id(),
        tool_name=tool_name,
        parameters=parameters,
    )


async def _run_worker_request(
    worker_manager: WorkerHandle,
    tool_name: str,
    parameters: dict,
) -> ToolResponse:
    """Send one worker request and return the raw worker response."""
    timeout = _tool_timeout(tool_name)
    request = _tool_request(tool_name, parameters)
    try:
        return await asyncio.wait_for(
            worker_manager.send_request(request),
            timeout=timeout,
        )
    except TimeoutError as exc:
        msg = f"Worker timeout for {tool_name} (>{timeout}s)"
        logger.error(msg)
        raise WorkerProxyError(msg) from exc


def _successful_worker_result(
    tool_name: str,
    response: ToolResponse,
) -> str:
    """Return the final worker result or raise a proxy error."""
    if response.status != "success":
        msg = f"Worker error for {tool_name}: {response.error_message}"
        logger.error(msg)
        raise WorkerProxyError(msg)
    return str(response.result)


def _email_parameters(params: object, smtp_config: dict) -> dict:
    """Return email parameters merged with SMTP configuration."""
    return {**params.model_dump(), **smtp_config}


def make_worker_proxy(
    worker_manager: WorkerHandle,
    tool_name: str,
    context_fn: Callable[[], Mapping[str, str]] | None = None,
) -> callable:
    """Create an async proxy handler for a worker-routed tool.

    Args:
        worker_manager: Worker dispatch handle for sending requests.
        tool_name: Name of the tool being proxied.
        context_fn: Optional broker execution-context reader; when the
            context carries ``domain_grant_host`` (the invocation runs
            under a domain-scoped grant), the host constraint is forwarded
            to the worker as the ``allowed_host`` parameter.

    Returns:
        Async handler that sends IPC request and returns result.
    """

    async def proxy_handler(params: object) -> str:
        """Send a tool request to the worker and return result.

        Args:
            params: Validated Pydantic input model.

        Returns:
            Result string from the worker.

        Raises:
            WorkerProxyError: On worker error or timeout.
        """
        parameters = params.model_dump()
        if context_fn is not None:
            granted_host = str(context_fn().get("domain_grant_host", "") or "")
            if granted_host:
                parameters["allowed_host"] = granted_host
        response = await _run_worker_request(
            worker_manager,
            tool_name,
            parameters,
        )
        return _successful_worker_result(tool_name, response)

    return proxy_handler


def make_email_proxy(
    worker_manager: WorkerHandle,
    smtp_config: dict | None,
) -> callable:
    """Create a proxy handler for email_send that injects SMTP config.

    Args:
        worker_manager: Worker manager for IPC.
        smtp_config: SMTP configuration dict with keys
            smtp_host, smtp_port, smtp_user, smtp_pass,
            from_addr. None if SMTP not configured.

    Returns:
        Async handler function.
    """

    async def email_proxy_handler(params: object) -> str:
        """Send an email_send request with injected SMTP config.

        Args:
            params: Validated Pydantic input model.

        Returns:
            Result string from the worker, or error if
            SMTP is not configured.

        Raises:
            WorkerProxyError: On worker error or timeout.
        """
        if smtp_config is None:
            return "Error: SMTP integration not configured"
        merged = _email_parameters(params, smtp_config)
        response = await _run_worker_request(worker_manager, "email_send", merged)
        return _successful_worker_result("email_send", response)

    return email_proxy_handler
