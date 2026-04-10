"""Schema for encrypted route-diagnostics storage.

Stores structured memory-routing decisions in a separate encrypted database.
This keeps operational diagnostics isolated from product memory data while
still using the same SQLCipher protection model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager

SCHEMA_VERSION = 1

CREATE_DIAGNOSTICS_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS diagnostics_schema_version (
    version INTEGER NOT NULL
)
"""

CREATE_MEMORY_ROUTE_DIAGNOSTICS_TABLE = """
CREATE TABLE IF NOT EXISTS memory_route_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    session_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    initial_intent TEXT NOT NULL,
    initial_confidence TEXT NOT NULL,
    initial_role_filter TEXT,
    initial_source TEXT NOT NULL,
    fallback_attempted INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    fallback_intent TEXT,
    fallback_confidence TEXT,
    fallback_role_filter TEXT,
    fallback_source TEXT,
    final_intent TEXT NOT NULL,
    final_confidence TEXT NOT NULL,
    final_role_filter TEXT,
    final_source TEXT NOT NULL,
    route_mismatch INTEGER NOT NULL DEFAULT 0,
    mismatch_label TEXT NOT NULL DEFAULT 'no_mismatch',
    used_long_term_mode INTEGER NOT NULL DEFAULT 0
)
"""

CREATE_MEMORY_ROUTE_SIGNAL_DIAGNOSTICS_TABLE = """
CREATE TABLE IF NOT EXISTS memory_route_signal_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnostic_id INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('initial', 'final')),
    signal_group TEXT NOT NULL,
    FOREIGN KEY (diagnostic_id) REFERENCES memory_route_diagnostics(id)
        ON DELETE CASCADE
)
"""

CREATE_NOTE_PROCESSING_DIAGNOSTICS_TABLE = """
CREATE TABLE IF NOT EXISTS record_processing_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    decision TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    record_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    message_count INTEGER NOT NULL DEFAULT 0,
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    messages TEXT NOT NULL DEFAULT '[]',
    injection_flagged INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    key_facts TEXT NOT NULL DEFAULT '[]',
    action_items TEXT NOT NULL DEFAULT '[]',
    memory_kinds TEXT NOT NULL DEFAULT '[]',
    prompt TEXT NOT NULL DEFAULT '[]',
    raw_output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
)
"""

CREATE_SUMMARY_PROCESSING_DIAGNOSTICS_TABLE = """
CREATE TABLE IF NOT EXISTS summary_processing_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    decision TEXT NOT NULL,
    summary_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    record_count INTEGER NOT NULL DEFAULT 0,
    source_record_ids TEXT NOT NULL DEFAULT '[]',
    records TEXT NOT NULL DEFAULT '[]',
    prompt TEXT NOT NULL DEFAULT '[]',
    raw_output TEXT NOT NULL DEFAULT '',
    main_point TEXT NOT NULL DEFAULT '',
    based_on TEXT NOT NULL DEFAULT '[]',
    trust_level TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    record_relations TEXT NOT NULL DEFAULT '[]',
    original_scope TEXT NOT NULL DEFAULT '',
    scope_adjustment_reason TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
)
"""

CREATE_MEMORY_ROUTE_DIAGNOSTICS_CREATED_AT_IDX = """
CREATE INDEX IF NOT EXISTS idx_memory_route_diagnostics_created_at
ON memory_route_diagnostics(created_at)
"""

CREATE_MEMORY_ROUTE_DIAGNOSTICS_FALLBACK_REASON_IDX = """
CREATE INDEX IF NOT EXISTS idx_memory_route_diagnostics_fallback_reason
ON memory_route_diagnostics(fallback_reason)
"""

CREATE_MEMORY_ROUTE_DIAGNOSTICS_MISMATCH_LABEL_IDX = """
CREATE INDEX IF NOT EXISTS idx_memory_route_diagnostics_mismatch_label
ON memory_route_diagnostics(mismatch_label)
"""

CREATE_MEMORY_ROUTE_SIGNAL_DIAGNOSTICS_STAGE_IDX = """
CREATE INDEX IF NOT EXISTS idx_memory_route_signal_diagnostics_stage
ON memory_route_signal_diagnostics(stage)
"""

CREATE_MEMORY_ROUTE_SIGNAL_DIAGNOSTICS_SIGNAL_GROUP_IDX = """
CREATE INDEX IF NOT EXISTS idx_memory_route_signal_diagnostics_signal_group
ON memory_route_signal_diagnostics(signal_group)
"""

CREATE_NOTE_PROCESSING_DIAGNOSTICS_CREATED_AT_IDX = """
CREATE INDEX IF NOT EXISTS idx_record_processing_diagnostics_created_at
ON record_processing_diagnostics(created_at)
"""

CREATE_NOTE_PROCESSING_DIAGNOSTICS_DECISION_IDX = """
CREATE INDEX IF NOT EXISTS idx_record_processing_diagnostics_decision
ON record_processing_diagnostics(decision)
"""

CREATE_SUMMARY_PROCESSING_DIAGNOSTICS_CREATED_AT_IDX = """
CREATE INDEX IF NOT EXISTS idx_summary_processing_diagnostics_created_at
ON summary_processing_diagnostics(created_at)
"""

CREATE_SUMMARY_PROCESSING_DIAGNOSTICS_DECISION_IDX = """
CREATE INDEX IF NOT EXISTS idx_summary_processing_diagnostics_decision
ON summary_processing_diagnostics(decision)
"""

SCHEMA_STATEMENTS = [
    CREATE_DIAGNOSTICS_SCHEMA_VERSION_TABLE,
    CREATE_MEMORY_ROUTE_DIAGNOSTICS_TABLE,
    CREATE_MEMORY_ROUTE_SIGNAL_DIAGNOSTICS_TABLE,
    CREATE_NOTE_PROCESSING_DIAGNOSTICS_TABLE,
    CREATE_SUMMARY_PROCESSING_DIAGNOSTICS_TABLE,
    CREATE_MEMORY_ROUTE_DIAGNOSTICS_CREATED_AT_IDX,
    CREATE_MEMORY_ROUTE_DIAGNOSTICS_FALLBACK_REASON_IDX,
    CREATE_MEMORY_ROUTE_DIAGNOSTICS_MISMATCH_LABEL_IDX,
    CREATE_MEMORY_ROUTE_SIGNAL_DIAGNOSTICS_STAGE_IDX,
    CREATE_MEMORY_ROUTE_SIGNAL_DIAGNOSTICS_SIGNAL_GROUP_IDX,
    CREATE_NOTE_PROCESSING_DIAGNOSTICS_CREATED_AT_IDX,
    CREATE_NOTE_PROCESSING_DIAGNOSTICS_DECISION_IDX,
    CREATE_SUMMARY_PROCESSING_DIAGNOSTICS_CREATED_AT_IDX,
    CREATE_SUMMARY_PROCESSING_DIAGNOSTICS_DECISION_IDX,
]


def _apply_schema_statements(
    db: DatabaseManager,
    statements: list[str],
) -> None:
    """Execute one ordered list of diagnostics schema statements."""
    for statement in statements:
        db.execute(statement)


def _insert_schema_version_if_missing(db: DatabaseManager) -> None:
    """Insert the diagnostics schema version row when the table is empty."""
    cursor = db.execute("SELECT count(*) FROM diagnostics_schema_version")
    if cursor.fetchone()[0] == 0:
        db.execute(
            "INSERT INTO diagnostics_schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )


def _retention_window(retention_days: int) -> str:
    """Return the SQLite relative time window for diagnostics pruning."""
    return f"-{retention_days} days"


def initialize_diagnostics_schema(
    db: DatabaseManager,
) -> None:
    """Create diagnostics tables and indexes if they do not exist."""
    _apply_schema_statements(db, SCHEMA_STATEMENTS)
    _insert_schema_version_if_missing(db)
    db.commit()


def prune_memory_route_diagnostics(
    db: DatabaseManager,
    retention_days: int,
) -> int:
    """Delete route diagnostics older than the configured retention window."""
    cursor = db.execute(
        """
        DELETE FROM memory_route_diagnostics
        WHERE created_at < strftime(
            '%Y-%m-%dT%H:%M:%f',
            'now',
            ?
        )
        """,
        (_retention_window(retention_days),),
    )
    db.commit()
    return int(cursor.rowcount or 0)
