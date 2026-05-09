"""Tests for separate runtime database schema."""

from __future__ import annotations

import sqlite3

from backend.security.runtime_schema import initialize_runtime_schema
from backend.security.schema import SCHEMA_STATEMENTS


class RuntimeSchemaDB:
    """Small sqlite-backed stand-in for DatabaseManager."""

    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute one SQL statement."""
        return self.connection.execute(sql, params)

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()


def test_core_schema_does_not_create_runtime_tables() -> None:
    """Core schema should not own short-lived assistant runtime tables."""
    core_schema = "\n".join(SCHEMA_STATEMENTS)

    assert "CREATE TABLE IF NOT EXISTS agent_runs" not in core_schema
    assert "CREATE TABLE IF NOT EXISTS pending_actions" not in core_schema


def test_runtime_schema_creates_lifecycle_tables() -> None:
    """Runtime schema owns agent runs and pending action lifecycle state."""
    db = RuntimeSchemaDB()

    initialize_runtime_schema(db)

    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "runtime_schema_version",
        "agent_runs",
        "pending_actions",
        "resume_snapshots",
    } <= tables

    version = db.execute(
        "SELECT version FROM runtime_schema_version LIMIT 1"
    ).fetchone()
    assert version == (1,)
