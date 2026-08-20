"""Scheduled task DB Proxy operations.

Provides module-level async functions that wrap DBProxyClient.send_operation
for scheduled task creation, listing, execution bookkeeping, and run history.
Each function receives a DBProxyClient instance as its first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def create_scheduled_task(
    client: DBProxyClient,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Create a new scheduled task.

    Args:
        client: DB Proxy client instance.
        params: Task creation parameters.

    Returns:
        Created task row.
    """
    return await client.send_operation("create_scheduled_task", params)


async def list_scheduled_tasks(
    client: DBProxyClient,
    status: str = "all",
) -> list[dict[str, Any]]:
    """List scheduled tasks.

    Args:
        client: DB Proxy client instance.
        status: Status filter (``all`` for every task).

    Returns:
        Matching task rows.
    """
    return await client._send_list_operation(
        "list_scheduled_tasks",
        result_key="tasks",
        parameters={"status": status},
    )


async def get_due_tasks(
    client: DBProxyClient,
    now: str,
) -> list[dict[str, Any]]:
    """Get tasks that are due for execution.

    Args:
        client: DB Proxy client instance.
        now: Current time (ISO-8601).

    Returns:
        Due task rows.
    """
    return await client._send_list_operation(
        "get_due_tasks",
        result_key="tasks",
        parameters={"now": now},
    )


async def advance_task_next_run(
    client: DBProxyClient,
    task_id: str,
    next_run_at: str,
    repeat_count: int,
) -> dict[str, Any]:
    """Advance next_run_at before execution.

    Args:
        client: DB Proxy client instance.
        task_id: Task to advance.
        next_run_at: Next run time (ISO-8601).
        repeat_count: Repeat counter for the new run.

    Returns:
        Updated task row.
    """
    return await client.send_operation(
        "advance_task_next_run",
        {
            "task_id": task_id,
            "next_run_at": next_run_at,
            "repeat_count": repeat_count,
        },
    )


async def mark_task_run(
    client: DBProxyClient,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Mark a task run as completed or failed.

    Args:
        client: DB Proxy client instance.
        params: Run result parameters.

    Returns:
        Updated task row.
    """
    return await client.send_operation("mark_task_run", params)


async def update_scheduled_task(
    client: DBProxyClient,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Update a scheduled task.

    Args:
        client: DB Proxy client instance.
        params: Task update parameters.

    Returns:
        Updated task row.
    """
    return await client.send_operation("update_scheduled_task", params)


async def delete_scheduled_task(
    client: DBProxyClient,
    task_id: str,
) -> dict[str, Any]:
    """Delete a scheduled task.

    Args:
        client: DB Proxy client instance.
        task_id: Task to delete.

    Returns:
        Deletion result.
    """
    return await client.send_operation("delete_scheduled_task", {"task_id": task_id})


async def record_task_run(
    client: DBProxyClient,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Record a single task run in the run history table.

    Args:
        client: DB Proxy client instance.
        params: Run record parameters.

    Returns:
        Recorded run row.
    """
    return await client.send_operation("record_task_run", params)


async def list_task_runs(
    client: DBProxyClient,
    task_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List recent runs for a task.

    Args:
        client: DB Proxy client instance.
        task_id: Task whose runs are listed.
        limit: Maximum runs returned.

    Returns:
        Run history rows.
    """
    return await client._send_list_operation(
        "list_task_runs",
        result_key="runs",
        parameters={"task_id": task_id, "limit": limit},
    )
