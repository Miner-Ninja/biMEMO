"""Tests for agent-run DB proxy handlers against the real runtime schema."""

import secrets

import pytest

from backend.processes.db_proxy.handlers.permissions import (
    make_list_agent_runs_handler,
)
from backend.security.database import DatabaseManager
from backend.security.runtime_schema import initialize_runtime_schema


@pytest.fixture
def master_key() -> bytearray:
    """Return a random master key for one test database."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db_path(tmp_path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "permissions-ops.db")


@pytest.fixture
def db(db_path: str, master_key: bytearray) -> DatabaseManager:
    """Return an initialized runtime database manager."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_runtime_schema(manager)
    yield manager
    manager.close()


def _insert_agent_run(
    db: DatabaseManager,
    *,
    run_id: str,
    conversation_id: str,
    status: str = "completed",
    tool_policy_version: str = "registry-v1",
    created_at: str = "2026-08-15T16:26:30.000Z",
) -> None:
    """Seed one agent_runs row with explicit ordering and policy version."""
    db.execute(
        "INSERT INTO agent_runs "
        "(id, conversation_id, session_id, channel, status, current_step, "
        "request_id, resume_payload, tool_policy_version, created_at, "
        "updated_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            conversation_id,
            f"session-{run_id}",
            "web",
            status,
            "final",
            f"request-{run_id}",
            "{}",
            tool_policy_version,
            created_at,
            created_at,
            created_at if status in ("completed", "failed") else None,
        ),
    )
    db.commit()


async def test_list_agent_runs_returns_full_record_shape(db):
    """Every agent_runs column reaches the API shape, including tool_policy_version."""
    _insert_agent_run(
        db,
        run_id="run-1",
        conversation_id="conversation-1",
        status="failed",
        tool_policy_version="legacy-v0",
    )
    handler = make_list_agent_runs_handler(db)

    result = await handler({"conversation_id": "conversation-1"})

    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["id"] == "run-1"
    assert run["conversation_id"] == "conversation-1"
    assert run["session_id"] == "session-run-1"
    assert run["channel"] == "web"
    assert run["status"] == "failed"
    assert run["current_step"] == "final"
    assert run["request_id"] == "request-run-1"
    assert run["resume_payload"] == {}
    assert run["tool_policy_version"] == "legacy-v0"
    assert run["created_at"] == "2026-08-15T16:26:30.000Z"
    assert run["completed_at"] == "2026-08-15T16:26:30.000Z"


async def test_list_agent_runs_orders_newest_first_and_filters_conversation(db):
    """Runs come back newest first and only for the requested conversation."""
    _insert_agent_run(
        db,
        run_id="run-old",
        conversation_id="conversation-1",
        created_at="2026-08-15T10:00:00.000Z",
    )
    _insert_agent_run(
        db,
        run_id="run-new",
        conversation_id="conversation-1",
        created_at="2026-08-15T16:00:00.000Z",
    )
    _insert_agent_run(
        db,
        run_id="run-other",
        conversation_id="conversation-2",
        created_at="2026-08-15T17:00:00.000Z",
    )
    handler = make_list_agent_runs_handler(db)

    result = await handler({"conversation_id": "conversation-1"})

    assert [run["id"] for run in result["runs"]] == ["run-new", "run-old"]
