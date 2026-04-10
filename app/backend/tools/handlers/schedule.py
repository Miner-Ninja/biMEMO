"""Scheduled task management tool handler.

Provides the schedule_task tool for creating, listing, pausing,
resuming, and removing scheduled tasks from within the chat.
Uses the DB proxy client to manage tasks in the scheduled_tasks table.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from backend.scheduler.parsing import (
    ScheduleParseError,
    compute_next_run,
    parse_schedule,
)
from backend.tools.builtin import ScheduleTaskInput


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)


def make_schedule_task_handler(
    db_client: object,
    broker: object | None = None,
) -> Callable[[dict[str, Any]], str]:
    """Create the schedule_task tool handler.

    Args:
        db_client: DB proxy client with scheduled task operations.
        broker: Optional ToolBroker — used to read the per-call execution
            context (conversation_id) so the task can be linked back to
            the originating chat.

    Returns:
        Async handler function.
    """

    async def handle_schedule_task(
        params: dict[str, Any] | ScheduleTaskInput,
    ) -> str:
        """Handle schedule_task tool calls."""
        p = _normalize_schedule_task_input(params)
        action = p.action.lower().strip()

        # Resolve originating conversation_id from broker context if the
        # LLM didn't pass one explicitly. This lets results be delivered
        # back to the chat where the user requested scheduling.
        if not p.conversation_id and broker is not None:
            ctx = getattr(broker, "current_context", None) or {}
            ctx_conv = ctx.get("conversation_id", "") if isinstance(ctx, dict) else ""
            if ctx_conv:
                p = p.model_copy(update={"conversation_id": ctx_conv})

        if action == "create":
            return await _create_task(db_client, p)
        if action == "list":
            return await _list_tasks(db_client)
        if action == "pause":
            return await _pause_task(db_client, p.task_id)
        if action == "resume":
            return await _resume_task(db_client, p.task_id)
        if action == "remove":
            return await _remove_task(db_client, p.task_id)

        return f"Unknown action: {action}. Valid: create, list, pause, resume, remove"

    return handle_schedule_task


def _normalize_schedule_task_input(
    params: dict[str, Any] | ScheduleTaskInput,
) -> ScheduleTaskInput:
    """Return a ScheduleTaskInput from raw params or a validated model."""
    if isinstance(params, ScheduleTaskInput):
        return params
    return ScheduleTaskInput(**params)


async def _create_task(db_client: object, p: ScheduleTaskInput) -> str:
    """Create a new scheduled task."""
    if not p.name:
        return "Error: name is required for create"
    if not p.prompt:
        return "Error: prompt is required for create"
    if not p.schedule:
        return "Error: schedule is required for create"
    if p.task_type not in ("reminder", "agent_task"):
        return "Error: task_type must be 'reminder' or 'agent_task'"

    try:
        schedule = parse_schedule(p.schedule)
    except ScheduleParseError as exc:
        return f"Error: {exc}"

    # Get user timezone from profile (used for cron expressions)
    user_timezone = "UTC"
    try:
        profile_result = await db_client.get_user_profile()
        profile = profile_result.get("profile", {})
        user_timezone = profile.get("timezone", "") or "UTC"
    except Exception:
        logger.warning("Failed to load user timezone, using UTC", exc_info=True)

    next_run = compute_next_run(schedule, timezone=user_timezone)

    # Validate allowed_tools JSON
    try:
        tools_list = json.loads(p.allowed_tools)
        if not isinstance(tools_list, list):
            return "Error: allowed_tools must be a JSON array"
    except json.JSONDecodeError:
        return "Error: allowed_tools must be valid JSON"

    result = await db_client.create_scheduled_task(
        {
            "name": p.name,
            "task_type": p.task_type,
            "prompt": p.prompt,
            "schedule_kind": schedule["kind"],
            "schedule_expr": p.schedule.strip(),
            "next_run_at": next_run,
            "allowed_tools": p.allowed_tools,
            "timezone": user_timezone,
            "conversation_id": p.conversation_id,
        }
    )

    task_id = result.get("task_id", "?")
    return (
        f"Created scheduled task '{p.name}' (id: {task_id})\n"
        f"Type: {p.task_type}\n"
        f"Schedule: {p.schedule} ({schedule['kind']}, tz: {user_timezone})\n"
        f"Next run: {next_run}"
    )


async def _list_tasks(db_client: object) -> str:
    """List all scheduled tasks."""
    tasks = await db_client.list_scheduled_tasks("all")
    if not tasks:
        return "No scheduled tasks found."

    lines = [f"Scheduled tasks ({len(tasks)}):"]
    for t in tasks:
        status_icon = {"active": "▶", "paused": "⏸", "completed": "✓"}.get(
            t["status"], "?"
        )
        lines.append(
            f"  {status_icon} {t['name']} ({t['id'][:8]}...)\n"
            f"    Type: {t['task_type']}, Schedule: {t['schedule_expr']}\n"
            f"    Status: {t['status']}, Next: {t.get('next_run_at', 'N/A')}"
        )
    return "\n".join(lines)


async def _pause_task(db_client: object, task_id: str) -> str:
    """Pause a scheduled task."""
    if not task_id:
        return "Error: task_id is required for pause"
    await db_client.update_scheduled_task(
        {
            "task_id": task_id,
            "status": "paused",
        }
    )
    return f"Task {task_id} paused."


async def _resume_task(db_client: object, task_id: str) -> str:
    """Resume a paused scheduled task."""
    if not task_id:
        return "Error: task_id is required for resume"
    await db_client.update_scheduled_task(
        {
            "task_id": task_id,
            "status": "active",
        }
    )
    return f"Task {task_id} resumed."


async def _remove_task(db_client: object, task_id: str) -> str:
    """Remove a scheduled task."""
    if not task_id:
        return "Error: task_id is required for remove"
    await db_client.delete_scheduled_task(task_id)
    return f"Task {task_id} removed."
