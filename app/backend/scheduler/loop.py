"""Scheduler background loop for checking and executing due tasks.

Runs as an asyncio background task in the Orchestrator process.
Checks for due tasks every 1 second, handles missed task detection,
pre-run advancement, and delegates execution to the executor.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.scheduler.executor import execute_task
from backend.scheduler.missed import compute_fast_forward, should_execute_overdue
from backend.scheduler.parsing import compute_next_run_after


if TYPE_CHECKING:
    from backend.scheduler.executor import SchedulerServices

logger = logging.getLogger(__name__)

# --- Constants ---
SCHEDULER_TICK_INTERVAL_SECONDS = 1.0
TASK_EXECUTION_TIMEOUT_SECONDS = 600.0


async def run_scheduler_loop(services: SchedulerServices) -> None:
    """Run the scheduler loop indefinitely.

    Checks for due tasks every second, executes them, and handles
    missed tasks on each tick. Designed to run as an asyncio background
    task alongside other Orchestrator loops.

    Args:
        services: Scheduler services bundle with DB client, orchestrator, etc.
    """
    logger.info(
        "Scheduler loop started (interval=%.1fs)",
        SCHEDULER_TICK_INTERVAL_SECONDS,
    )
    while True:
        try:
            await _tick(services)
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")
            raise
        except Exception:
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(SCHEDULER_TICK_INTERVAL_SECONDS)


async def _tick(services: SchedulerServices) -> None:
    """Execute one scheduler tick: find and process due tasks."""
    now = datetime.now(tz=UTC)
    now_iso = now.isoformat()

    due_tasks = await services.db_client.get_due_tasks(now_iso)
    if not due_tasks:
        return

    for task in due_tasks:
        try:
            await _process_due_task(services, task, now)
        except Exception:
            logger.exception(
                "Failed to process scheduled task %s (%s)",
                task.get("id", "?"),
                task.get("name", "?"),
            )


async def _process_due_task(
    services: SchedulerServices,
    task: dict,
    now: datetime,
) -> None:
    """Process a single due task: advance, execute, and mark completion."""
    task_id = task["id"]
    task_name = task["name"]
    schedule_kind = task["schedule_kind"]

    # Check if overdue task should execute or be skipped
    if not should_execute_overdue(task, now):
        next_run = compute_fast_forward(task, now)
        if next_run:
            logger.info(
                "Skipping stale task %s (%s), fast-forwarding to %s",
                task_id,
                task_name,
                next_run,
            )
            await services.db_client.advance_task_next_run(
                task_id=task_id,
                next_run_at=next_run,
                repeat_count=task["repeat_count"],
            )
        return

    # Pre-run advance for recurring tasks (at-most-once guarantee)
    new_repeat_count = task["repeat_count"] + 1
    if schedule_kind != "once":
        next_run = compute_next_run_after(
            schedule_kind,
            task["schedule_expr"],
            after=now,
            timezone=task.get("timezone") or "UTC",
        )
        if next_run:
            await services.db_client.advance_task_next_run(
                task_id=task_id,
                next_run_at=next_run,
                repeat_count=new_repeat_count,
            )

    # Check repeat limit
    repeat_limit = task.get("repeat_limit")
    if repeat_limit is not None and new_repeat_count >= repeat_limit:
        await services.db_client.mark_task_run(
            {
                "task_id": task_id,
                "status": "completed",
                "last_run_at": now.isoformat(),
                "last_status": "skipped",
            }
        )
        logger.info(
            "Task %s (%s) reached repeat limit %d, marked completed",
            task_id,
            task_name,
            repeat_limit,
        )
        return

    # Execute the task with timeout
    logger.info("Executing scheduled task %s (%s)", task_id, task_name)
    started_at = datetime.now(tz=UTC)
    timed_out = False
    try:
        result_text, success = await asyncio.wait_for(
            execute_task(services, task),
            timeout=TASK_EXECUTION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        result_text = "Task execution timed out"
        success = False
        timed_out = True
        logger.warning(
            "Task %s (%s) timed out after %ds",
            task_id,
            task_name,
            TASK_EXECUTION_TIMEOUT_SECONDS,
        )
    finished_at = datetime.now(tz=UTC)
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)

    # Mark run completion
    update_params: dict = {
        "task_id": task_id,
        "last_run_at": now.isoformat(),
        "last_status": "ok" if success else "error",
        "last_error": "" if success else result_text[:500],
    }

    # Complete one-shot tasks
    if schedule_kind == "once" and not task.get("_authorization_quarantined"):
        update_params["status"] = "completed"

    await services.db_client.mark_task_run(update_params)

    # Persist run history entry (best-effort: don't fail the loop on write error)
    if timed_out:
        run_status = "timeout"
    elif success and result_text.strip().startswith("[SILENT]"):
        run_status = "silent"
    elif success:
        run_status = "ok"
    else:
        run_status = "error"
    try:
        if not services.atomic_task_result_committed:
            await services.db_client.record_task_run(
                {
                    "task_id": task_id,
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "status": run_status,
                    "result_text": result_text if success else "",
                    "error_message": "" if success else result_text[:1000],
                    "duration_ms": duration_ms,
                }
            )
    except Exception:
        logger.exception("Failed to record run history for task %s", task_id)

    logger.info(
        "Task %s (%s) finished: %s",
        task_id,
        task_name,
        "ok" if success else "error",
    )
