"""Scheduled task DB proxy handler factories.

Provides CRUD operations for the scheduled_tasks table used by
the internal task scheduler. Handles creation, listing, due-task
queries, next-run advancement, run completion marking, status
updates, and deletion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..common import OperationHandler, validate_params
from ..params import (
    ActivateToolRegistryParams,
    AdvanceTaskParams,
    CreateScheduledTaskParams,
    DeleteScheduledTaskParams,
    GetDueTasksParams,
    ListScheduledTasksParams,
    ListTaskRunsParams,
    RecordTaskRunParams,
    UpdateScheduledTaskParams,
    validate_revisioned_tool_authorizations,
)


if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.security.database import DatabaseManager


class ToolRevisionAuthority:
    """One-time registry revision projection owned by the live orchestrator."""

    def __init__(self) -> None:
        """Start inactive so scheduler writes fail closed."""
        self._revisions: Mapping[str, str] | None = None

    def activate(self, revisions: dict[str, str]) -> None:
        """Accept the finalized live projection exactly once."""
        if self._revisions is not None:
            if dict(self._revisions) == revisions:
                return
            raise RuntimeError("tool registry projection is already active")
        if not revisions or any(
            not name or not revision for name, revision in revisions.items()
        ):
            raise ValueError("tool registry projection is invalid")
        self._revisions = MappingProxyType(dict(revisions))

    def require(self) -> Mapping[str, str]:
        """Return the active projection or reject persistence."""
        if self._revisions is None:
            raise RuntimeError("tool registry projection is not active")
        return self._revisions


def make_activate_tool_registry_handler(
    authority: ToolRevisionAuthority,
) -> OperationHandler:
    """Activate the sole scheduler persistence projection once."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        parsed = validate_params(params, ActivateToolRegistryParams)
        authority.activate(parsed.revisions)
        return {"activated": True, "tool_count": len(parsed.revisions)}

    return handle


def _current_utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO format."""
    return datetime.now(tz=UTC).isoformat()


def _task_item(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one scheduled_tasks row into the API shape."""
    return {
        "id": row[0],
        "name": row[1],
        "task_type": row[2],
        "prompt": row[3],
        "schedule_kind": row[4],
        "schedule_expr": row[5],
        "next_run_at": row[6],
        "status": row[7],
        "last_run_at": row[8],
        "last_status": row[9],
        "last_error": row[10],
        "repeat_limit": row[11],
        "repeat_count": row[12],
        "allowed_tools": row[13],
        "conversation_id": row[14],
        "deliver": row[15],
        "timezone": row[16],
        "created_at": row[17],
    }


_SELECT_COLUMNS = (
    "id, name, task_type, prompt, schedule_kind, schedule_expr, "
    "next_run_at, status, last_run_at, last_status, last_error, "
    "repeat_limit, repeat_count, allowed_tools, conversation_id, "
    "deliver, timezone, created_at"
)


def make_create_scheduled_task_handler(
    db: DatabaseManager,
    authority: ToolRevisionAuthority,
) -> OperationHandler:
    """Create handler for create_scheduled_task operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Create a new scheduled task."""
        p = validate_params(params, CreateScheduledTaskParams)
        validate_revisioned_tool_authorizations(
            p.allowed_tools,
            authority.require(),
        )
        task_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO scheduled_tasks "
            "(id, name, task_type, prompt, schedule_kind, schedule_expr, "
            "next_run_at, allowed_tools, conversation_id, deliver, "
            "repeat_limit, timezone, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                p.name,
                p.task_type,
                p.prompt,
                p.schedule_kind,
                p.schedule_expr,
                p.next_run_at,
                p.allowed_tools,
                p.conversation_id,
                p.deliver,
                p.repeat_limit,
                p.timezone,
                _current_utc_timestamp(),
            ),
        )
        db.commit()
        return {"task_id": task_id, "next_run_at": p.next_run_at}

    return handle


def make_list_scheduled_tasks_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for list_scheduled_tasks operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List scheduled tasks with optional status filter."""
        p = validate_params(params, ListScheduledTasksParams)
        if p.status in ("active", "paused", "completed"):
            rows = db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM scheduled_tasks "  # noqa: S608 — dynamic identifiers only; values bound
                "WHERE status = ? ORDER BY next_run_at ASC LIMIT 100",
                (p.status,),
            ).fetchall()
        else:
            rows = db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM scheduled_tasks "  # noqa: S608 — dynamic identifiers only; values bound
                "ORDER BY next_run_at ASC LIMIT 100",
            ).fetchall()
        return {"tasks": [_task_item(row) for row in rows]}

    return handle


def make_get_due_tasks_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_due_tasks operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Get all tasks that are due for execution."""
        p = validate_params(params, GetDueTasksParams)
        rows = db.execute(
            f"SELECT {_SELECT_COLUMNS} FROM scheduled_tasks "  # noqa: S608 — dynamic identifiers only; values bound
            "WHERE next_run_at <= ? AND status = 'active' "
            "ORDER BY next_run_at ASC",
            (p.now,),
        ).fetchall()
        return {"tasks": [_task_item(row) for row in rows]}

    return handle


def make_advance_task_next_run_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for advance_task_next_run operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Advance next_run_at and increment repeat_count before execution."""
        p = validate_params(params, AdvanceTaskParams)
        db.execute(
            "UPDATE scheduled_tasks SET next_run_at = ?, repeat_count = ? WHERE id = ?",
            (p.next_run_at, p.repeat_count, p.task_id),
        )
        db.commit()
        return {"ok": True}

    return handle


def make_mark_task_run_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for mark_task_run operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Mark a task run as completed or failed."""
        p = validate_params(params, UpdateScheduledTaskParams)
        updates: list[str] = []
        values: list[Any] = []
        if p.status is not None:
            updates.append("status = ?")
            values.append(p.status)
        if p.last_run_at is not None:
            updates.append("last_run_at = ?")
            values.append(p.last_run_at)
        if p.last_status is not None:
            updates.append("last_status = ?")
            values.append(p.last_status)
        if p.last_error is not None:
            updates.append("last_error = ?")
            values.append(p.last_error)
        if p.repeat_count is not None:
            updates.append("repeat_count = ?")
            values.append(p.repeat_count)
        if p.next_run_at is not None:
            updates.append("next_run_at = ?")
            values.append(p.next_run_at)
        if not updates:
            return {"ok": True}
        values.append(p.task_id)
        db.execute(
            f"UPDATE scheduled_tasks SET {', '.join(updates)} WHERE id = ?",  # noqa: S608 — dynamic identifiers only; values bound
            tuple(values),
        )
        db.commit()
        return {"ok": True}

    return handle


def make_update_scheduled_task_handler(
    db: DatabaseManager,
    authority: ToolRevisionAuthority,
) -> OperationHandler:
    """Create handler for update_scheduled_task operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Update one or more fields on a scheduled task."""
        p = validate_params(params, UpdateScheduledTaskParams)
        if p.allowed_tools is not None:
            validate_revisioned_tool_authorizations(
                p.allowed_tools,
                authority.require(),
            )
        updates: list[str] = []
        values: list[Any] = []
        if p.status is not None:
            updates.append("status = ?")
            values.append(p.status)
        if p.next_run_at is not None:
            updates.append("next_run_at = ?")
            values.append(p.next_run_at)
        if p.name is not None:
            updates.append("name = ?")
            values.append(p.name)
        if p.prompt is not None:
            updates.append("prompt = ?")
            values.append(p.prompt)
        if p.schedule_kind is not None:
            updates.append("schedule_kind = ?")
            values.append(p.schedule_kind)
        if p.schedule_expr is not None:
            updates.append("schedule_expr = ?")
            values.append(p.schedule_expr)
        if p.allowed_tools is not None:
            updates.append("allowed_tools = ?")
            values.append(p.allowed_tools)
        if p.repeat_limit is not None:
            updates.append("repeat_limit = ?")
            values.append(p.repeat_limit)
        if p.timezone is not None:
            updates.append("timezone = ?")
            values.append(p.timezone)
        if not updates:
            return {"ok": True}
        values.append(p.task_id)
        db.execute(
            f"UPDATE scheduled_tasks SET {', '.join(updates)} WHERE id = ?",  # noqa: S608 — dynamic identifiers only; values bound
            tuple(values),
        )
        db.commit()
        return {"ok": True}

    return handle


def make_delete_scheduled_task_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for delete_scheduled_task operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete a scheduled task."""
        p = validate_params(params, DeleteScheduledTaskParams)
        db.execute(
            "DELETE FROM scheduled_tasks WHERE id = ?",
            (p.task_id,),
        )
        db.commit()
        return {"ok": True}

    return handle


# --- Run history ---

# How many run records to keep per task. Older ones are pruned on insert.
TASK_RUN_HISTORY_LIMIT = 100


def _run_item(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one scheduled_task_runs row into the API shape."""
    return {
        "id": row[0],
        "task_id": row[1],
        "started_at": row[2],
        "finished_at": row[3],
        "status": row[4],
        "result_text": row[5],
        "error_message": row[6],
        "duration_ms": row[7],
    }


def make_record_task_run_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for record_task_run operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Insert a run record and prune old history for the task."""
        import uuid

        p = validate_params(params, RecordTaskRunParams)
        run_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO scheduled_task_runs "
            "(id, task_id, started_at, finished_at, status, "
            "result_text, error_message, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                p.task_id,
                p.started_at,
                p.finished_at,
                p.status,
                p.result_text[:4000],
                p.error_message[:1000],
                p.duration_ms,
            ),
        )
        # Prune oldest runs beyond the per-task history limit
        db.execute(
            "DELETE FROM scheduled_task_runs WHERE task_id = ? "
            "AND id NOT IN ("
            "  SELECT id FROM scheduled_task_runs "
            "  WHERE task_id = ? "
            "  ORDER BY started_at DESC LIMIT ?"
            ")",
            (p.task_id, p.task_id, TASK_RUN_HISTORY_LIMIT),
        )
        db.commit()
        return {"run_id": run_id}

    return handle


def make_list_task_runs_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for list_task_runs operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List recent runs for a task, newest first."""
        p = validate_params(params, ListTaskRunsParams)
        limit = max(1, min(p.limit, TASK_RUN_HISTORY_LIMIT))
        rows = db.execute(
            "SELECT id, task_id, started_at, finished_at, status, "
            "result_text, error_message, duration_ms "
            "FROM scheduled_task_runs "
            "WHERE task_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (p.task_id, limit),
        ).fetchall()
        return {"runs": [_run_item(row) for row in rows]}

    return handle
