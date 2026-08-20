"""Admin and integration config DB Proxy operations.

Provides module-level async functions that wrap DBProxyClient.send_operation
for database statistics, integration configuration reads, and rate-limit
call bookkeeping. Each function receives a DBProxyClient instance as its
first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def get_stats(client: DBProxyClient) -> dict[str, int]:
    """Get database statistics.

    Args:
        client: DB Proxy client instance.

    Returns:
        Dict with table name -> row count.
    """
    return await client.send_operation("get_stats")


async def get_integration_config(
    client: DBProxyClient,
    service_type: str,
) -> dict[str, Any]:
    """Return integration + all credentials for a service_type.

    Args:
        client: DB Proxy client instance.
        service_type: Integration type (e.g. 'openrouter', 'ollama', 'telegram').

    Returns:
        Dict with 'integration' (or None) and 'credentials' keys.
    """
    return await client.send_operation(
        "get_integration_config",
        {
            "service_type": service_type,
        },
    )


async def record_rate_limit_call(
    client: DBProxyClient,
    tool_name: str,
    called_at: float,
) -> dict[str, Any]:
    """Persist a rate limit call timestamp.

    Args:
        client: DB Proxy client instance.
        tool_name: Name of the tool that was called.
        called_at: Unix timestamp of the call.

    Returns:
        Dict with 'ok' key.
    """
    return await client.send_operation(
        "record_rate_limit_call",
        {
            "tool_name": tool_name,
            "called_at": called_at,
        },
    )


async def get_recent_rate_limit_calls(
    client: DBProxyClient,
    tool_name: str,
    since: float,
) -> dict[str, Any]:
    """Return recent rate limit call timestamps for a tool.

    Args:
        client: DB Proxy client instance.
        tool_name: Name of the tool.
        since: Unix timestamp; return calls after this time.

    Returns:
        Dict with 'timestamps' list.
    """
    return await client.send_operation(
        "get_recent_rate_limit_calls",
        {
            "tool_name": tool_name,
            "since": since,
        },
    )
