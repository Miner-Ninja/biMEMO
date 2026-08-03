"""Tests for outdated-summary retention and the pruning DB-proxy handlers."""

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest

from backend.memory.retrieval.fulltext import create_fts_tables
from backend.memory.storage.pruning import should_prune_summary
from backend.processes.db_proxy.handlers.memory import (
    make_prune_stale_summaries_handler,
    make_prune_unlinked_records_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


GRACE = 30
PRUNE_PARAMS = {"grace_days": GRACE}


@pytest.fixture
def master_key() -> bytearray:
    """Return a random master key for one test database."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db_path(tmp_path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "prune-memory.db")


@pytest.fixture
def db(db_path: str, master_key: bytearray) -> DatabaseManager:
    """Return an initialized database manager with FTS tables."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    create_fts_tables(manager)
    yield manager
    manager.close()


def _iso(days_ago: int) -> str:
    """Return a DB-compatible ISO timestamp `days_ago` days in the past."""
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _insert_summary(
    db: DatabaseManager,
    summary_id: str,
    *,
    days_ago: int,
    record_relations: list[dict] | None = None,
    source_record_ids: list[str] | None = None,
) -> None:
    """Insert one summary row directly."""
    db.execute(
        "INSERT INTO summaries "
        "(id, main_point, based_on, trust_level, summary_scope, "
        "trust_level_score, source_record_ids, record_relations, created_at) "
        "VALUES (?, ?, '[]', 'high', 'long_term', 3, ?, ?, ?)",
        (
            summary_id,
            f"main point {summary_id}",
            json.dumps(source_record_ids or []),
            json.dumps(record_relations or []),
            _iso(days_ago),
        ),
    )
    db.commit()


def _insert_record(db: DatabaseManager, record_id: str, *, days_ago: int) -> None:
    """Insert one record row directly (with its parent conversation)."""
    db.execute("INSERT OR IGNORE INTO conversations (id) VALUES ('conv-1')")
    db.execute(
        "INSERT INTO records "
        "(id, conversation_id, session_id, summary, created_at) "
        "VALUES (?, 'conv-1', 'sess-1', ?, ?)",
        (record_id, f"record {record_id}", _iso(days_ago)),
    )
    db.commit()


def _insert_orphaned_summary(
    db: DatabaseManager, summary_id: str, *, days_ago: int
) -> None:
    """Insert a summary that derives to 'orphaned' (support to a missing record)."""
    ghost = f"ghost-{summary_id}"
    _insert_summary(
        db,
        summary_id,
        days_ago=days_ago,
        source_record_ids=[ghost],
        record_relations=[{"record_id": ghost, "relation": "supports"}],
    )


def _insert_relational_summary(
    db: DatabaseManager, summary_id: str, *, days_ago: int, relation: str
) -> None:
    """Insert a summary linked to an active record by `relation`.

    An 'outdates' relation derives to 'outdated'; 'conflicts' to 'conflicted'.
    """
    record_id = f"rec-{summary_id}"
    _insert_record(db, record_id, days_ago=days_ago)
    _insert_summary(
        db,
        summary_id,
        days_ago=days_ago,
        source_record_ids=[record_id],
        record_relations=[{"record_id": record_id, "relation": relation}],
    )


def _summary_ids(db: DatabaseManager) -> set[str]:
    return {r[0] for r in db.execute("SELECT id FROM summaries").fetchall()}


def _record_ids(db: DatabaseManager) -> set[str]:
    return {r[0] for r in db.execute("SELECT id FROM records").fetchall()}


def _relations_of(db: DatabaseManager, summary_id: str) -> str:
    row = db.execute(
        "SELECT record_relations FROM summaries WHERE id = ?", (summary_id,)
    ).fetchone()
    return row[0] if row else ""


# --- pure policy helper ------------------------------------------------------


def test_should_prune_outdated_over_grace():
    """Outdated past the grace period is pruned."""
    assert should_prune_summary(status="outdated", age_days=GRACE + 1, grace_days=GRACE)


def test_should_prune_outdated_under_grace():
    """Outdated within the grace period is kept."""
    assert not should_prune_summary(
        status="outdated", age_days=GRACE - 1, grace_days=GRACE
    )


def test_should_prune_orphaned_over_grace():
    """Orphaned past the grace period is pruned."""
    assert should_prune_summary(status="orphaned", age_days=GRACE + 1, grace_days=GRACE)


def test_should_prune_orphaned_under_grace():
    """Orphaned within the grace period is kept."""
    assert not should_prune_summary(
        status="orphaned", age_days=GRACE - 1, grace_days=GRACE
    )


def test_should_prune_conflicted_is_kept():
    """Conflicted is kept at any age (handled by reconciliation)."""
    assert not should_prune_summary(
        status="conflicted", age_days=9999, grace_days=GRACE
    )


def test_should_prune_fresh_is_kept():
    """Fresh is never pruned."""
    assert not should_prune_summary(status="fresh", age_days=9999, grace_days=GRACE)


# --- handler behavior --------------------------------------------------------


def test_outdated_pruned_after_grace(db: DatabaseManager):
    """Outdated summaries are pruned only after the grace period."""
    _insert_relational_summary(db, "s-old", days_ago=40, relation="outdates")
    _insert_relational_summary(db, "s-young", days_ago=5, relation="outdates")

    handler = make_prune_stale_summaries_handler(db)
    result = asyncio.run(handler(PRUNE_PARAMS))

    assert result["deleted"] == 1
    assert "s-old" not in _summary_ids(db)
    assert "s-young" in _summary_ids(db)


def test_orphaned_pruned_after_grace(db: DatabaseManager):
    """Orphaned summaries are pruned after the grace period, not before."""
    _insert_orphaned_summary(db, "s-old", days_ago=40)
    _insert_orphaned_summary(db, "s-young", days_ago=5)

    handler = make_prune_stale_summaries_handler(db)
    result = asyncio.run(handler(PRUNE_PARAMS))

    assert result["deleted"] == 1
    assert _summary_ids(db) == {"s-young"}


def test_conflicted_is_retained_untouched(db: DatabaseManager):
    """A conflicted summary is retained with its relations unchanged."""
    _insert_relational_summary(db, "s-conf", days_ago=999, relation="conflicts")
    before = _relations_of(db, "s-conf")

    handler = make_prune_stale_summaries_handler(db)
    result = asyncio.run(handler(PRUNE_PARAMS))

    assert result["deleted"] == 0
    assert "s-conf" in _summary_ids(db)
    assert _relations_of(db, "s-conf") == before


def test_prune_stale_summaries_is_idempotent(db: DatabaseManager):
    """Running the prune twice deletes once, then is a safe no-op."""
    _insert_relational_summary(db, "s-old", days_ago=40, relation="outdates")
    handler = make_prune_stale_summaries_handler(db)

    first = asyncio.run(handler(PRUNE_PARAMS))
    second = asyncio.run(handler(PRUNE_PARAMS))

    assert first["deleted"] == 1
    assert second["deleted"] == 0
    assert "s-old" not in _summary_ids(db)


# --- prune_unlinked_records --------------------------------------------------


def test_prune_unlinked_records_deletes_aged_unreferenced(db: DatabaseManager):
    """Old unreferenced records are pruned; linked or young ones are kept."""
    _insert_record(db, "r-unlinked-old", days_ago=40)
    _insert_record(db, "r-linked-old", days_ago=40)
    _insert_record(db, "r-unlinked-young", days_ago=1)
    _insert_summary(db, "s-1", days_ago=1, source_record_ids=["r-linked-old"])

    handler = make_prune_unlinked_records_handler(db)
    result = asyncio.run(handler({"age_days": 30}))

    assert result["deleted"] == 1
    assert _record_ids(db) == {"r-linked-old", "r-unlinked-young"}


def test_prune_unlinked_records_keeps_records_linked_by_relations(
    db: DatabaseManager,
):
    """A record referenced only via record_relations is retained."""
    _insert_record(db, "r-rel-old", days_ago=40)
    _insert_summary(
        db,
        "s-1",
        days_ago=1,
        record_relations=[{"record_id": "r-rel-old", "relation": "supports"}],
    )

    handler = make_prune_unlinked_records_handler(db)
    result = asyncio.run(handler({"age_days": 30}))

    assert result["deleted"] == 0
    assert _record_ids(db) == {"r-rel-old"}


# --- wiring / schema / constant guards ---------------------------------------


def test_prune_operations_are_registered():
    """Both prune operations are in the memory operation allow-list."""
    from backend.processes.db_proxy.registry import SOCKET_A_OPERATIONS

    assert "prune_stale_summaries" in SOCKET_A_OPERATIONS
    assert "prune_unlinked_records" in SOCKET_A_OPERATIONS


def test_pruning_loop_is_wired_into_orchestrator_setup():
    """The orchestrator background-task starter accepts the pruning loop."""
    from backend.processes.orchestrator.dialog_recorder_setup import (
        _start_dialog_recorder_background_tasks as starter,
    )

    # Read argument names from the code object to avoid evaluating the
    # module's runtime-deferred annotations.
    code = starter.__code__
    argnames = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
    assert "run_pruning_loop_fn" in argnames


def test_summaries_schema_has_no_status_column(db: DatabaseManager):
    """Retention must not add a persistent status column."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(summaries)")}
    assert "revalidation_status" not in columns
    assert "status" not in columns


def test_retention_constants_are_present_and_scope_free():
    """The retention constant is the grace period; TTL/scope constants are gone."""
    from backend.memory.storage import pruning

    assert hasattr(pruning, "OUTDATED_GRACE_DAYS")
    assert not hasattr(pruning, "SUMMARY_PRUNE_AGE_DAYS")
    assert not hasattr(pruning, "SHORT_TERM_TTL_DAYS")
    assert not hasattr(pruning, "LONG_TERM_BROKEN_GRACE_DAYS")
