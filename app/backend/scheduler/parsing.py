"""Schedule expression parsing and next-run computation.

Supports three schedule types: one-shot (ISO timestamp), interval
(e.g. '30m', '2h', '1d'), and cron expressions (e.g. '0 9 * * *').
All timestamps are UTC ISO 8601 strings.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


logger = logging.getLogger(__name__)


# --- Interval pattern: number + unit (m=minutes, h=hours, d=days) ---
_INTERVAL_RE = re.compile(r"^(\d+)\s*(m|h|d)$", re.IGNORECASE)

# --- Unit multipliers in minutes ---
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440}


class ScheduleParseError(Exception):
    """Raised when a schedule expression cannot be parsed."""


def _resolve_timezone(timezone: str | None) -> ZoneInfo:
    """Return a ZoneInfo for the given name, falling back to UTC."""
    if not timezone:
        return UTC
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError, ValueError:
        logger.warning("Invalid timezone %r — falling back to UTC", timezone)
        return UTC


def parse_schedule(expr: str) -> dict[str, str | int]:
    """Parse a schedule expression into a structured dict.

    Args:
        expr: Schedule expression string.

    Returns:
        Dict with keys:
        - kind: 'once', 'interval', or 'cron'
        - For 'once': run_at (ISO timestamp)
        - For 'interval': minutes (int)
        - For 'cron': cron_expr (string)

    Raises:
        ScheduleParseError: If expression is not valid.
    """
    stripped = expr.strip()

    # Try interval first (e.g. "30m", "2h", "1d")
    match = _INTERVAL_RE.match(stripped)
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        minutes = value * _UNIT_MINUTES[unit]
        if minutes < 1:
            msg = f"Interval must be at least 1 minute, got {stripped}"
            raise ScheduleParseError(msg)
        return {"kind": "interval", "minutes": minutes}

    # Try cron expression (5 fields)
    if croniter.is_valid(stripped):
        return {"kind": "cron", "cron_expr": stripped}

    # Try ISO timestamp
    try:
        dt = datetime.fromisoformat(stripped)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return {"kind": "once", "run_at": dt.isoformat()}
    except ValueError:
        pass

    msg = (
        f"Invalid schedule expression: '{stripped}'. "
        "Valid formats: ISO timestamp (2026-04-10T09:00:00), "
        "interval (30m, 2h, 1d), or cron (0 9 * * *)"
    )
    raise ScheduleParseError(msg)


def compute_next_run(
    schedule: dict[str, str | int],
    now: datetime | None = None,
    timezone: str | None = None,
) -> str:
    """Compute the next run time for a schedule.

    Args:
        schedule: Parsed schedule dict from parse_schedule().
        now: Current time (defaults to UTC now).
        timezone: Timezone for cron evaluation (defaults to UTC).

    Returns:
        ISO timestamp string in UTC for the next run.
    """
    if now is None:
        now = datetime.now(tz=UTC)

    kind = schedule["kind"]

    if kind == "once":
        return str(schedule["run_at"])

    if kind == "interval":
        minutes = int(schedule["minutes"])
        return (now + timedelta(minutes=minutes)).astimezone(UTC).isoformat()

    # kind == "cron" — evaluate in user timezone, return UTC
    tz = _resolve_timezone(timezone)
    local_now = now.astimezone(tz)
    cron = croniter(str(schedule["cron_expr"]), local_now)
    next_local = cron.get_next(datetime)
    return next_local.astimezone(UTC).isoformat()


def compute_next_run_after(
    schedule_kind: str,
    schedule_expr: str,
    after: datetime | None = None,
    timezone: str | None = None,
) -> str | None:
    """Compute the next run time from raw schedule fields.

    Args:
        schedule_kind: 'once', 'interval', or 'cron'.
        schedule_expr: Raw expression string.
        after: Reference time (defaults to UTC now).
        timezone: Timezone for cron evaluation (defaults to UTC).

    Returns:
        ISO timestamp string in UTC, or None for completed one-shot tasks.
    """
    if after is None:
        after = datetime.now(tz=UTC)

    if schedule_kind == "once":
        return None

    if schedule_kind == "interval":
        match = _INTERVAL_RE.match(schedule_expr.strip())
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            minutes = value * _UNIT_MINUTES[unit]
            return (after + timedelta(minutes=minutes)).astimezone(UTC).isoformat()
        return None

    if schedule_kind == "cron":
        tz = _resolve_timezone(timezone)
        local_after = after.astimezone(tz)
        cron = croniter(schedule_expr, local_after)
        next_local = cron.get_next(datetime)
        return next_local.astimezone(UTC).isoformat()

    return None


def schedule_period_seconds(
    schedule_kind: str,
    schedule_expr: str,
) -> float | None:
    """Return the approximate period in seconds for a schedule.

    Args:
        schedule_kind: 'once', 'interval', or 'cron'.
        schedule_expr: Raw expression string.

    Returns:
        Period in seconds, or None for one-shot.
    """
    if schedule_kind == "once":
        return None

    if schedule_kind == "interval":
        match = _INTERVAL_RE.match(schedule_expr.strip())
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            return value * _UNIT_MINUTES[unit] * 60.0
        return None

    if schedule_kind == "cron":
        now = datetime.now(tz=UTC)
        cron = croniter(schedule_expr, now)
        next1 = cron.get_next(datetime)
        next2 = cron.get_next(datetime)
        return (next2 - next1).total_seconds()

    return None
