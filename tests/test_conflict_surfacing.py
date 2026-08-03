"""Tests for surfacing conflicted summaries as pending-conflict signals."""

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from backend.memory.retrieval.fulltext import create_fts_tables
from backend.memory.storage.pruning import surface_conflicted_summaries
from backend.memory.summary_distiller.constants import (
    TRACE_DECISION_CONFLICT_PENDING,
)
from backend.processes.db_proxy.handlers.diagnostic import (
    make_conflicted_summary_flag_handler,
)
from backend.processes.db_proxy.handlers.memory import (
    make_list_conflicted_summaries_handler,
)
from backend.security.database import DatabaseManager
from backend.security.diagnostics_schema import initialize_diagnostics_schema
from backend.security.schema import initialize_schema


@pytest.fixture
def master_key() -> bytearray:
    """Return a random master key for one test database."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db(tmp_path, master_key: bytearray) -> DatabaseManager:
    """Return an initialized database manager with FTS tables."""
    manager = DatabaseManager(str(tmp_path / "conflict.db"))
    manager.open(master_key)
    initialize_schema(manager)
    create_fts_tables(manager)
    yield manager
    manager.close()


def _iso(days_ago: int) -> str:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _insert_record(db: DatabaseManager, record_id: str) -> None:
    db.execute("INSERT OR IGNORE INTO conversations (id) VALUES ('conv-1')")
    db.execute(
        "INSERT INTO records "
        "(id, conversation_id, session_id, summary, created_at) "
        "VALUES (?, 'conv-1', 'sess-1', ?, ?)",
        (record_id, f"record {record_id}", _iso(1)),
    )
    db.commit()


def _insert_summary(
    db: DatabaseManager,
    summary_id: str,
    *,
    record_relations: list[dict],
    source_record_ids: list[str],
) -> None:
    db.execute(
        "INSERT INTO summaries "
        "(id, main_point, based_on, trust_level, summary_scope, "
        "trust_level_score, source_record_ids, record_relations, created_at) "
        "VALUES (?, ?, '[]', 'high', 'long_term', 3, ?, ?, ?)",
        (
            summary_id,
            f"main point {summary_id}",
            json.dumps(source_record_ids),
            json.dumps(record_relations),
            _iso(1),
        ),
    )
    db.commit()


def _insert_conflicted(db: DatabaseManager, summary_id: str) -> None:
    """Insert a summary that derives to 'conflicted' (conflict to active record)."""
    record_id = f"rec-{summary_id}"
    _insert_record(db, record_id)
    _insert_summary(
        db,
        summary_id,
        source_record_ids=[record_id],
        record_relations=[{"record_id": record_id, "relation": "conflicts"}],
    )


def _insert_orphaned(db: DatabaseManager, summary_id: str) -> None:
    ghost = f"ghost-{summary_id}"
    _insert_summary(
        db,
        summary_id,
        source_record_ids=[ghost],
        record_relations=[{"record_id": ghost, "relation": "supports"}],
    )


# --- list_conflicted_summaries handler ---------------------------------------


def test_list_conflicted_returns_only_conflicted(db: DatabaseManager):
    """The handler returns conflicted summaries and ignores the rest."""
    _insert_conflicted(db, "s-conf")
    _insert_orphaned(db, "s-orph")

    handler = make_list_conflicted_summaries_handler(db)
    result = asyncio.run(handler({}))

    summaries = result["summaries"]
    assert [s["summary_id"] for s in summaries] == ["s-conf"]
    assert summaries[0]["main_point"] == "main point s-conf"


def test_list_conflicted_is_read_only(db: DatabaseManager):
    """The handler does not mutate the summary it reports."""
    _insert_conflicted(db, "s-conf")
    before = db.execute(
        "SELECT record_relations FROM summaries WHERE id = 's-conf'"
    ).fetchone()[0]

    handler = make_list_conflicted_summaries_handler(db)
    asyncio.run(handler({}))

    after = db.execute(
        "SELECT record_relations FROM summaries WHERE id = 's-conf'"
    ).fetchone()[0]
    assert after == before
    assert db.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 1


# --- surface_conflicted_summaries client orchestration -----------------------


def _client_with(conflicted: list[dict], already: set[str]) -> AsyncMock:
    """Build a mock DB proxy client scripted for the surfacing flow.

    ``already`` is the set of summary ids that the idempotent
    ``conflicted_summary_flag`` operation reports as already surfaced.
    """
    client = AsyncMock()

    def respond(name: str, params: dict) -> dict:
        if name == "list_conflicted_summaries":
            return {"summaries": conflicted}
        if name == "conflicted_summary_flag":
            new = params["summary_id"] not in already
            return {"surfaced": 1 if new else 0}
        return {}

    client.send_operation.side_effect = respond
    return client


def _record_calls(client: AsyncMock) -> list:
    return [
        call
        for call in client.send_operation.call_args_list
        if call[0][0] == "conflicted_summary_flag"
    ]


def test_surface_records_signal_for_new_conflict():
    """A new conflicted summary produces one pending-conflict record call."""
    client = _client_with(
        conflicted=[{"summary_id": "s1", "main_point": "mp1"}],
        already=set(),
    )

    surfaced = asyncio.run(surface_conflicted_summaries(client))

    assert surfaced == 1
    calls = _record_calls(client)
    assert len(calls) == 1
    assert calls[0][0][1] == {"summary_id": "s1", "main_point": "mp1"}


def test_surface_counts_only_newly_recorded():
    """Server-side dedup: an already-surfaced summary is not counted again."""
    client = _client_with(
        conflicted=[
            {"summary_id": "s1", "main_point": "mp1"},
            {"summary_id": "s2", "main_point": "mp2"},
        ],
        already={"s1"},
    )

    surfaced = asyncio.run(surface_conflicted_summaries(client))

    # Both are offered to the idempotent op; only s2 is newly recorded.
    assert surfaced == 1
    assert [c[0][1]["summary_id"] for c in _record_calls(client)] == ["s1", "s2"]


def test_surface_no_conflicts_records_nothing():
    """No conflicted summaries means no record calls."""
    client = _client_with(conflicted=[], already=set())

    surfaced = asyncio.run(surface_conflicted_summaries(client))

    assert surfaced == 0
    assert _record_calls(client) == []


# --- registration ------------------------------------------------------------


def test_list_conflicted_operation_is_registered():
    """The read operation is in the memory operation allow-list."""
    from backend.processes.db_proxy.registry import SOCKET_A_OPERATIONS

    assert "list_conflicted_summaries" in SOCKET_A_OPERATIONS


def test_conflicted_summary_flag_is_on_orchestrator_socket():
    """Both surfacing operations are reachable on the orchestrator socket."""
    from backend.processes.db_proxy.registry import SOCKET_A_OPERATIONS

    assert "conflicted_summary_flag" in SOCKET_A_OPERATIONS
    assert "list_conflicted_summaries" in SOCKET_A_OPERATIONS


# --- conflicted_summary_flag handler (idempotency) ---------------------------


@pytest.fixture
def diagnostics_db(tmp_path, master_key: bytearray) -> DatabaseManager:
    """Return an initialized diagnostics database manager."""
    manager = DatabaseManager(str(tmp_path / "diagnostics.db"))
    manager.open(master_key)
    initialize_diagnostics_schema(manager)
    yield manager
    manager.close()


def _pending_count(db: DatabaseManager, summary_id: str) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM summary_processing_diagnostics "
        "WHERE decision = ? AND summary_id = ?",
        (TRACE_DECISION_CONFLICT_PENDING, summary_id),
    ).fetchone()[0]


def test_conflicted_summary_flag_is_idempotent(diagnostics_db: DatabaseManager):
    """A second record for the same summary inserts nothing."""
    handler = make_conflicted_summary_flag_handler(diagnostics_db)

    first = asyncio.run(handler({"summary_id": "s1", "main_point": "mp1"}))
    second = asyncio.run(handler({"summary_id": "s1", "main_point": "mp1"}))

    assert first["surfaced"] == 1
    assert second["surfaced"] == 0
    assert _pending_count(diagnostics_db, "s1") == 1
