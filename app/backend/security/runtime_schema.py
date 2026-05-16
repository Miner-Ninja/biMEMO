"""Runtime database schema for short-lived assistant execution state."""

# ruff: noqa: S608

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


RUNTIME_SCHEMA_VERSION = 2
RUN_TERMINAL_STATUSES = frozenset({"cancelled", "completed", "failed", "stopped"})
ACTION_TERMINAL_STATUSES = frozenset({"completed", "failed", "denied", "expired"})

CREATE_RUNTIME_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS runtime_schema_version (
    version INTEGER NOT NULL
)
"""

CREATE_AGENT_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    resume_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    completed_at TEXT
)
"""

CREATE_AGENT_RUNS_CONVERSATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation
ON agent_runs(conversation_id, created_at DESC)
"""

CREATE_AGENT_RUN_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    delivered_at TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
    UNIQUE(run_id, sequence)
)
"""

CREATE_AGENT_RUN_EVENTS_CONVERSATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_agent_run_events_conversation
ON agent_run_events(conversation_id, created_at DESC)
"""

CREATE_AGENT_RUN_EVENTS_REQUEST_IDX = """
CREATE INDEX IF NOT EXISTS idx_agent_run_events_request
ON agent_run_events(request_id, sequence)
"""

CREATE_AGENT_RUN_EVENTS_RUN_IDX = """
CREATE INDEX IF NOT EXISTS idx_agent_run_events_run
ON agent_run_events(run_id, sequence)
"""

CREATE_PENDING_ACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT '',
    preview_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    expires_at TEXT,
    decided_at TEXT,
    executed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
)
"""

CREATE_PENDING_ACTIONS_RUN_IDX = """
CREATE INDEX IF NOT EXISTS idx_pending_actions_run
ON pending_actions(run_id, status, created_at)
"""

CREATE_PENDING_ACTIONS_STATUS_IDX = """
CREATE INDEX IF NOT EXISTS idx_pending_actions_status
ON pending_actions(status, expires_at)
"""

CREATE_RESUME_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS resume_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    expires_at TEXT,
    consumed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (action_id) REFERENCES pending_actions(id) ON DELETE CASCADE
)
"""

CREATE_RESUME_SNAPSHOTS_ACTION_IDX = """
CREATE INDEX IF NOT EXISTS idx_resume_snapshots_action
ON resume_snapshots(action_id, created_at DESC)
"""

CREATE_RESUME_SNAPSHOTS_EXPIRATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_resume_snapshots_expiration
ON resume_snapshots(expires_at, consumed_at)
"""

RUNTIME_SCHEMA_STATEMENTS = [
    CREATE_RUNTIME_SCHEMA_VERSION_TABLE,
    CREATE_AGENT_RUNS_TABLE,
    CREATE_AGENT_RUNS_CONVERSATION_IDX,
    CREATE_AGENT_RUN_EVENTS_TABLE,
    CREATE_AGENT_RUN_EVENTS_CONVERSATION_IDX,
    CREATE_AGENT_RUN_EVENTS_REQUEST_IDX,
    CREATE_AGENT_RUN_EVENTS_RUN_IDX,
    CREATE_PENDING_ACTIONS_TABLE,
    CREATE_PENDING_ACTIONS_RUN_IDX,
    CREATE_PENDING_ACTIONS_STATUS_IDX,
    CREATE_RESUME_SNAPSHOTS_TABLE,
    CREATE_RESUME_SNAPSHOTS_ACTION_IDX,
    CREATE_RESUME_SNAPSHOTS_EXPIRATION_IDX,
]


def initialize_runtime_schema(db: DatabaseManager) -> None:
    """Create runtime database tables and indexes if they do not exist."""
    for statement in RUNTIME_SCHEMA_STATEMENTS:
        db.execute(statement)
    _ensure_runtime_schema_version(db)
    db.commit()


def _ensure_runtime_schema_version(db: DatabaseManager) -> None:
    """Insert or update the stored runtime schema version row."""
    cursor = db.execute("SELECT version FROM runtime_schema_version LIMIT 1")
    version_row = cursor.fetchone()
    if version_row is None:
        db.execute(
            "INSERT INTO runtime_schema_version (version) VALUES (?)",
            (RUNTIME_SCHEMA_VERSION,),
        )
    elif version_row[0] < RUNTIME_SCHEMA_VERSION:
        db.execute(
            "UPDATE runtime_schema_version SET version = ?",
            (RUNTIME_SCHEMA_VERSION,),
        )


def prune_runtime_state(
    db: DatabaseManager,
    *,
    now: str | None = None,
    snapshot_retention_hours: int = 24,
    run_retention_days: int = 7,
) -> dict[str, int]:
    """Expire and delete stale short-lived assistant runtime rows."""
    current_time = now or _sqlite_now(db)
    snapshot_cutoff = _time_cutoff(
        current_time,
        hours=snapshot_retention_hours,
    )
    run_cutoff = _time_cutoff(current_time, days=run_retention_days)

    expired = db.execute(
        "UPDATE pending_actions SET status = 'expired', decision = 'expired', "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
        "decided_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
        "WHERE status = 'waiting' AND expires_at IS NOT NULL AND expires_at <= ?",
        (current_time,),
    ).rowcount
    db.execute(
        "UPDATE resume_snapshots SET "
        "consumed_at = COALESCE(consumed_at, strftime('%Y-%m-%dT%H:%M:%f', 'now')), "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
        "WHERE action_id IN ("
        "SELECT id FROM pending_actions "
        f"WHERE status IN ({_status_placeholders(ACTION_TERMINAL_STATUSES)})"
        ") AND consumed_at IS NULL",
        tuple(sorted(ACTION_TERMINAL_STATUSES)),
    )
    deleted_snapshots = db.execute(
        "DELETE FROM resume_snapshots WHERE "
        "(expires_at IS NOT NULL AND expires_at <= ?) "
        "OR (consumed_at IS NOT NULL AND consumed_at <= ?)",
        (current_time, snapshot_cutoff),
    ).rowcount
    db.execute(
        "DELETE FROM agent_run_events WHERE run_id IN ("
        "SELECT id FROM agent_runs WHERE "
        f"status IN ({_status_placeholders(RUN_TERMINAL_STATUSES)}) "
        "AND completed_at IS NOT NULL AND completed_at <= ?"
        ")",
        (*tuple(sorted(RUN_TERMINAL_STATUSES)), run_cutoff),
    )
    deleted_runs = db.execute(
        "DELETE FROM agent_runs WHERE "
        f"status IN ({_status_placeholders(RUN_TERMINAL_STATUSES)}) "
        "AND completed_at IS NOT NULL AND completed_at <= ?",
        (*tuple(sorted(RUN_TERMINAL_STATUSES)), run_cutoff),
    ).rowcount
    db.commit()
    return {
        "expired_pending_actions": expired,
        "deleted_resume_snapshots": deleted_snapshots,
        "deleted_agent_runs": deleted_runs,
    }


def _sqlite_now(db: DatabaseManager) -> str:
    """Return the current timestamp in the same format used by the schema."""
    row = db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f', 'now')").fetchone()
    return str(row[0]) if row else ""


def _time_cutoff(now: str, *, hours: int = 0, days: int = 0) -> str:
    """Return an ISO timestamp offset backwards from ``now``."""
    try:
        current = datetime.fromisoformat(now)
    except ValueError:
        return now
    return (current - timedelta(hours=hours, days=days)).isoformat(
        timespec="milliseconds"
    )


def _status_placeholders(statuses: frozenset[str]) -> str:
    """Return SQL placeholders for one fixed status set."""
    return ", ".join("?" for _ in statuses)
