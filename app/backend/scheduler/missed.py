"""Missed task detection and recovery on restart.

When the scheduler starts (or after downtime), overdue tasks need
handling. Tasks overdue by less than one schedule period get one
catch-up execution. Tasks overdue by more than one period are
fast-forwarded to the next future run time without execution.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from backend.scheduler.parsing import (
    compute_next_run_after,
    schedule_period_seconds,
)


logger = logging.getLogger(__name__)


def should_execute_overdue(
    task: dict,
    now: datetime | None = None,
) -> bool:
    """Determine whether an overdue task should execute or be skipped.

    Args:
        task: Scheduled task dict with schedule_kind, schedule_expr,
              next_run_at fields.
        now: Current time (defaults to UTC now).

    Returns:
        True if the task should execute (catch-up), False if it
        should be skipped and fast-forwarded.
    """
    if now is None:
        now = datetime.now(tz=UTC)

    schedule_kind = task["schedule_kind"]

    # One-shot tasks always execute when overdue
    if schedule_kind == "once":
        return True

    next_run_at = datetime.fromisoformat(task["next_run_at"])
    overdue_seconds = (now - next_run_at).total_seconds()

    period = schedule_period_seconds(schedule_kind, task["schedule_expr"])
    if period is None or period <= 0:
        return True

    # Execute if overdue by less than one full period
    return overdue_seconds < period


def compute_fast_forward(
    task: dict,
    now: datetime | None = None,
) -> str | None:
    """Compute the next future run time for a stale task.

    Args:
        task: Scheduled task dict.
        now: Current time (defaults to UTC now).

    Returns:
        ISO timestamp for the next future run, or None if the
        task cannot be rescheduled (one-shot).
    """
    if now is None:
        now = datetime.now(tz=UTC)

    return compute_next_run_after(
        task["schedule_kind"],
        task["schedule_expr"],
        after=now,
        timezone=task.get("timezone") or "UTC",
    )
