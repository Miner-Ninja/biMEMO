"""Admin routes for managing scheduled tasks.

CRUD endpoints for the scheduled_tasks table used by the internal
task scheduler. List, create, update (pause/resume), and delete
scheduled tasks via the DB Proxy Socket B client.
"""

# ruff: noqa: B008

import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from backend.models.requests import (  # noqa: TC001
    ScheduledTaskCreateRequest,
    ScheduledTaskUpdateRequest,
)
from backend.scheduler.parsing import (
    ScheduleParseError,
    compute_next_run,
    parse_schedule,
)

from ..deps import _get_admin_client, _get_orchestrator_client, _require_auth


if TYPE_CHECKING:
    from backend.channels.jwt_auth import TokenClaims

    from ..clients import AdminDBClient, OrchestratorClient


logger = logging.getLogger(__name__)

scheduled_tasks_router = APIRouter(prefix="/admin/scheduled-tasks")


@scheduled_tasks_router.get("")
async def list_scheduled_tasks(
    status: str = "all",
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """List scheduled tasks with optional status filter.

    Args:
        status: Filter by status — all, active, paused, completed.
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.

    Returns:
        Dict with key ``tasks`` holding the list of task records.
    """
    del claims
    if status not in ("all", "active", "paused", "completed"):
        raise HTTPException(
            status_code=422,
            detail="Invalid status filter",
        )
    result = await admin.list_scheduled_tasks(status=status)
    if isinstance(result, dict):
        return {"tasks": result.get("tasks", [])}
    return {"tasks": result}


@scheduled_tasks_router.post("")
async def create_scheduled_task(
    body: ScheduledTaskCreateRequest,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    orchestrator: OrchestratorClient = Depends(_get_orchestrator_client),
) -> dict:
    """Create a new scheduled task.

    Args:
        body: Task creation payload.
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.
        orchestrator: Runtime registry IPC client.

    Returns:
        Dict with the created task_id and computed next_run_at.
    """
    del claims
    try:
        schedule = parse_schedule(body.schedule)
    except ScheduleParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Resolve user timezone from profile (used for cron evaluation)
    user_timezone = "UTC"
    try:
        profile_result = await admin.get_user_profile()
        profile = (
            profile_result.get("profile", {})
            if isinstance(profile_result, dict)
            else {}
        )
        user_timezone = profile.get("timezone", "") or "UTC"
    except Exception:
        logger.warning("Failed to load user timezone, using UTC", exc_info=True)

    next_run = compute_next_run(schedule, timezone=user_timezone)

    try:
        approved_tools = await orchestrator.authorize_scheduled_tools(
            body.allowed_tools
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await admin.create_scheduled_task(
        {
            "name": body.name,
            "task_type": body.task_type,
            "prompt": body.prompt,
            "schedule_kind": schedule["kind"],
            "schedule_expr": body.schedule.strip(),
            "next_run_at": next_run,
            "allowed_tools": json.dumps(approved_tools),
            "timezone": user_timezone,
            "conversation_id": body.conversation_id,
            "repeat_limit": body.repeat_limit,
        }
    )
    return {
        "task_id": result.get("task_id", ""),
        "next_run_at": result.get("next_run_at", next_run),
    }


@scheduled_tasks_router.patch("/{task_id}")
async def update_scheduled_task(
    task_id: str,
    body: ScheduledTaskUpdateRequest,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    orchestrator: OrchestratorClient = Depends(_get_orchestrator_client),
) -> dict:
    """Update a scheduled task.

    Supports partial updates: status (pause/resume), name, prompt,
    schedule (re-parsed and recomputes next_run_at), allowed_tools,
    repeat_limit.

    Args:
        task_id: Task identifier.
        body: Update payload.
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.
        orchestrator: Runtime registry IPC client.

    Returns:
        Dict with success indicator.
    """
    del claims
    update_params: dict = {"task_id": task_id}
    if body.status is not None:
        update_params["status"] = body.status
    if body.name is not None:
        update_params["name"] = body.name
    if body.prompt is not None:
        update_params["prompt"] = body.prompt
    if body.allowed_tools is not None:
        try:
            approved_tools = await orchestrator.authorize_scheduled_tools(
                body.allowed_tools
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        update_params["allowed_tools"] = json.dumps(approved_tools)
    if body.repeat_limit is not None:
        update_params["repeat_limit"] = body.repeat_limit

    # If schedule is provided, re-parse and recompute next_run_at using
    # the user's timezone.
    if body.schedule is not None:
        try:
            schedule = parse_schedule(body.schedule)
        except ScheduleParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        user_timezone = "UTC"
        try:
            profile_result = await admin.get_user_profile()
            profile = (
                profile_result.get("profile", {})
                if isinstance(profile_result, dict)
                else {}
            )
            user_timezone = profile.get("timezone", "") or "UTC"
        except Exception:
            logger.warning("Failed to load user timezone, using UTC", exc_info=True)
        next_run = compute_next_run(schedule, timezone=user_timezone)
        update_params["schedule_kind"] = schedule["kind"]
        update_params["schedule_expr"] = body.schedule.strip()
        update_params["next_run_at"] = next_run
        update_params["timezone"] = user_timezone

    if len(update_params) == 1:  # only task_id, nothing to update
        raise HTTPException(
            status_code=422,
            detail="No fields to update",
        )

    await admin.update_scheduled_task(update_params)
    return {"ok": True}


@scheduled_tasks_router.delete("/{task_id}")
async def delete_scheduled_task(
    task_id: str,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Delete a scheduled task.

    Args:
        task_id: Task identifier.
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.

    Returns:
        Dict with success indicator.
    """
    del claims
    await admin.delete_scheduled_task(task_id)
    return {"ok": True}


@scheduled_tasks_router.get("/{task_id}/runs")
async def list_task_runs(
    task_id: str,
    limit: int = 20,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """List recent execution history for a scheduled task.

    Args:
        task_id: Task identifier.
        limit: Maximum number of runs to return (default 20, max 100).
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.

    Returns:
        Dict with key ``runs`` holding the run records (newest first).
    """
    del claims
    safe_limit = max(1, min(limit, 100))
    result = await admin.list_task_runs(task_id, limit=safe_limit)
    if isinstance(result, dict):
        return {"runs": result.get("runs", [])}
    return {"runs": result}


# --- Tool catalog (used by scheduler UI to populate allowlist selector) ---


tools_catalog_router = APIRouter(prefix="/admin/tools")


@tools_catalog_router.get("")
async def list_available_tools(
    claims: TokenClaims = Depends(_require_auth),
    orchestrator: OrchestratorClient = Depends(_get_orchestrator_client),
) -> dict:
    """Return the catalog of registered tool definitions.

    Used by the scheduler UI to populate the allowlist multi-select.
    Returns presentation-only names and descriptions. Permission policy remains
    private to the orchestrator registry.
    """
    del claims
    return {"tools": await orchestrator.list_tool_catalog()}
