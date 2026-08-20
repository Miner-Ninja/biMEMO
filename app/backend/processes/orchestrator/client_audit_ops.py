"""Audit log DB Proxy operations.

Provides module-level async functions that wrap DBProxyClient.send_operation
for audit log entry persistence and hash-chain verification. Each function
receives a DBProxyClient instance as its first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def write_audit(
    client: DBProxyClient,
    event_type: str,
    tool_name: str | None = None,
    parameters: str | None = None,
    result: str | None = None,
    zone: str | None = None,
    duration_ms: float | None = None,
) -> int:
    """Append an audit log entry.

    Args:
        client: DB Proxy client instance.
        event_type: Audit event type.
        tool_name: Tool name.
        parameters: JSON parameters (redacted).
        result: Result summary.
        zone: Trust zone.
        duration_ms: Execution time.

    Returns:
        Entry ID.
    """
    return await client._send_int_operation(
        "write_audit",
        result_key="entry_id",
        parameters={
            "event_type": event_type,
            "tool_name": tool_name,
            "parameters": parameters,
            "result": result,
            "zone": zone,
            "duration_ms": duration_ms,
        },
    )


async def verify_chain(client: DBProxyClient) -> bool:
    """Verify audit log hash chain integrity.

    Args:
        client: DB Proxy client instance.

    Returns:
        True if chain is valid.
    """
    return await client._send_bool_operation(
        "verify_chain",
        result_key="valid",
    )
