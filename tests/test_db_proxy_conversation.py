"""Tests for conversation DB proxy handlers."""

import asyncio
import secrets

import pytest

from backend.processes.db_proxy.handlers.conversation import (
    make_reset_stuck_processing_states_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


@pytest.fixture
def master_key() -> bytearray:
    """Return a random master key for one test database."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db_path(tmp_path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "conversation-ops.db")


@pytest.fixture
def db(db_path: str, master_key: bytearray) -> DatabaseManager:
    """Return an initialized database manager."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    yield manager
    manager.close()


def _insert_conversation(
    db: DatabaseManager,
    *,
    conversation_id: str,
    processing_status: str = "idle",
    processing_stage: str = "",
    processing_started_at: str | None = None,
) -> None:
    """Seed one conversation row with a specific processing state."""
    db.execute(
        "INSERT INTO conversations "
        "(id, title, processing_status, processing_stage, processing_started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            conversation_id,
            conversation_id,
            processing_status,
            processing_stage,
            processing_started_at,
        ),
    )
    db.commit()


def _load_processing_state(
    db: DatabaseManager,
    conversation_id: str,
) -> tuple[str, str, str | None]:
    """Return one conversation's stored processing state tuple."""
    row = db.execute(
        "SELECT processing_status, processing_stage, processing_started_at "
        "FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    return (row[0], row[1], row[2])


class TestResetStuckProcessingStates:
    """Tests for the startup cleanup handler."""

    def test_active_rows_are_reset_idle_rows_untouched(
        self,
        db: DatabaseManager,
    ) -> None:
        """Active rows flip to idle while idle rows stay as they are."""
        _insert_conversation(
            db,
            conversation_id="active-one",
            processing_status="active",
            processing_stage="Processing...",
            processing_started_at="2026-04-24T20:00:00.000",
        )
        _insert_conversation(
            db,
            conversation_id="active-two",
            processing_status="active",
            processing_stage="Transcribing...",
            processing_started_at="2026-04-24T20:01:00.000",
        )
        _insert_conversation(
            db,
            conversation_id="idle-one",
            processing_status="idle",
            processing_stage="",
            processing_started_at=None,
        )

        handler = make_reset_stuck_processing_states_handler(db)
        result = asyncio.run(handler(None))

        assert result == {"reset_count": 2}
        assert _load_processing_state(db, "active-one") == ("idle", "", None)
        assert _load_processing_state(db, "active-two") == ("idle", "", None)
        assert _load_processing_state(db, "idle-one") == ("idle", "", None)

    def test_no_active_rows_returns_zero(
        self,
        db: DatabaseManager,
    ) -> None:
        """When nothing is stuck the handler reports zero resets."""
        _insert_conversation(
            db,
            conversation_id="idle-one",
            processing_status="idle",
        )
        handler = make_reset_stuck_processing_states_handler(db)
        result = asyncio.run(handler(None))

        assert result == {"reset_count": 0}

    def test_handler_ignores_params(self, db: DatabaseManager) -> None:
        """The handler accepts but ignores an arbitrary params dict."""
        _insert_conversation(
            db,
            conversation_id="active-one",
            processing_status="active",
            processing_stage="Processing...",
            processing_started_at="2026-04-24T20:00:00.000",
        )
        handler = make_reset_stuck_processing_states_handler(db)
        result = asyncio.run(handler({"ignored": "value"}))

        assert result == {"reset_count": 1}
