"""Tests for graph lifecycle maintenance (stage 4).

Asserts the graph-memory-lifecycle contract: R1 (S1.1-S1.5),
R2 (S2.1-S2.2), R3 (S3.1-S3.2), R5 (S5.1-S5.3).
"""

import os

from backend.memory.retrieval.fulltext import create_fts_tables
from backend.processes.db_proxy.handlers.graph import (
    make_get_graph_stats_handler,
    make_prune_graph_handler,
    make_save_assertion_handler,
    make_save_entity_handler,
)
from backend.processes.db_proxy.handlers.memory import (
    make_prune_unlinked_records_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


def _open_db(tmp_path):
    """Create a schema-complete encrypted test database."""
    db = DatabaseManager(str(tmp_path / "lifecycle.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    create_fts_tables(db)
    return db


async def _entity(db, name="vim", entity_type="tool"):
    handler = make_save_entity_handler(db)
    result = await handler(
        {"entity_type": entity_type, "canonical_name": name, "aliases": []}
    )
    return result["entity_id"]


async def _assertion(db, subject_id, **overrides):
    handler = make_save_assertion_handler(db)
    params = {
        "subject_entity_id": subject_id,
        "relation": "prefers",
        "assertion_text": "user prefers vim",
        "object_value": "vim",
    }
    params.update(overrides)
    result = await handler(params)
    return result["assertion_id"]


def _backdate_invalidation(db, assertion_id, days):
    db.execute(
        "UPDATE assertions SET invalid_from = "
        "strftime('%Y-%m-%dT%H:%M:%f', 'now', ?) WHERE id = ?",
        (f"-{days} days", assertion_id),
    )
    db.commit()


def _backdate_entity(db, entity_id, hours):
    db.execute(
        "UPDATE entities SET created_at = "
        "strftime('%Y-%m-%dT%H:%M:%f', 'now', ?) WHERE id = ?",
        (f"-{hours} hours", entity_id),
    )
    db.commit()


class TestGraphPrune:
    """R1 — assertion retention and orphan-entity cleanup."""

    async def test_old_invalidated_deleted_and_references_cleared(self, tmp_path):
        """S1.1 — retention removal clears superseder references."""
        db = _open_db(tmp_path)
        subject = await _entity(db)
        old = await _assertion(db, subject)
        new = await _assertion(
            db,
            subject,
            assertion_text="user prefers emacs",
            object_value="emacs",
        )
        db.execute(
            "UPDATE assertions SET invalidated_by_assertion_id = ? WHERE id = ?",
            (old, new),
        )
        _backdate_invalidation(db, old, 120)
        prune = make_prune_graph_handler(db)
        result = await prune({})
        assert result["assertions_deleted"] == 1
        assert result["references_cleared"] == 1
        remaining = db.execute("SELECT id FROM assertions").fetchall()
        assert [row[0] for row in remaining] == [new]
        ref = db.execute(
            "SELECT invalidated_by_assertion_id FROM assertions WHERE id = ?",
            (new,),
        ).fetchone()[0]
        assert ref is None
        db.close()

    async def test_recently_invalidated_kept(self, tmp_path):
        """S1.2 — fresh invalidations survive."""
        db = _open_db(tmp_path)
        subject = await _entity(db)
        recent = await _assertion(db, subject)
        _backdate_invalidation(db, recent, 5)
        prune = make_prune_graph_handler(db)
        result = await prune({})
        assert result["assertions_deleted"] == 0
        db.close()

    async def test_orphan_grace(self, tmp_path):
        """S1.3 — old orphans die, young orphans live."""
        db = _open_db(tmp_path)
        old_orphan = await _entity(db, name="old-orphan")
        _backdate_entity(db, old_orphan, 48)
        await _entity(db, name="young-orphan")
        prune = make_prune_graph_handler(db)
        result = await prune({})
        assert result["entities_deleted"] == 1
        names = {
            row[0]
            for row in db.execute("SELECT canonical_name FROM entities").fetchall()
        }
        assert names == {"young-orphan"}
        db.close()

    async def test_object_side_entity_not_orphan(self, tmp_path):
        """S1.4 — an object-role entity is not an orphan."""
        db = _open_db(tmp_path)
        subject = await _entity(db, name="user", entity_type="person")
        target = await _entity(db, name="vim")
        _backdate_entity(db, subject, 48)
        _backdate_entity(db, target, 48)
        await _assertion(
            db,
            subject,
            object_entity_id=target,
            object_value=None,
        )
        prune = make_prune_graph_handler(db)
        result = await prune({})
        assert result["entities_deleted"] == 0
        db.close()

    async def test_valid_assertions_never_deleted(self, tmp_path):
        """S1.5 — valid assertions survive every pass."""
        db = _open_db(tmp_path)
        subject = await _entity(db)
        await _assertion(db, subject)
        prune = make_prune_graph_handler(db)
        await prune({})
        count = db.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
        assert count == 1
        db.close()


class TestRecordProtection:
    """R2 — record pruner follows assertion evidence."""

    async def _seed_old_record(self, db, record_id):
        db.execute("INSERT OR IGNORE INTO conversations (id) VALUES ('c1')")
        db.execute(
            "INSERT INTO records "
            "(id, conversation_id, session_id, summary, created_at) "
            "VALUES (?, 'c1', 's1', 'old record', "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now', '-120 days'))",
            (record_id,),
        )
        db.commit()

    async def test_record_cited_by_assertion_survives(self, tmp_path):
        """S2.1 — assertion evidence protects an old record."""
        db = _open_db(tmp_path)
        await self._seed_old_record(db, "rec-cited")
        subject = await _entity(db)
        await _assertion(db, subject, source_record_ids=["rec-cited"])
        prune = make_prune_unlinked_records_handler(db)
        await prune({"age_days": 60})
        row = db.execute("SELECT id FROM records WHERE id = 'rec-cited'").fetchone()
        assert row is not None
        db.close()

    async def test_uncited_old_record_deleted(self, tmp_path):
        """S2.2 — unprotected old records are removed."""
        db = _open_db(tmp_path)
        await self._seed_old_record(db, "rec-loose")
        prune = make_prune_unlinked_records_handler(db)
        await prune({"age_days": 60})
        row = db.execute("SELECT id FROM records WHERE id = 'rec-loose'").fetchone()
        assert row is None
        db.close()


class TestGraphStats:
    """R3 — statistics snapshot."""

    async def test_stats_reflect_state(self, tmp_path):
        """S3.1 — populated graph is fully described."""
        db = _open_db(tmp_path)
        subject = await _entity(db, name="user", entity_type="person")
        await _entity(db, name="stray-topic", entity_type="topic")
        kept = await _assertion(
            db,
            subject,
            source_record_ids=["missing-record"],
        )
        dead = await _assertion(
            db,
            subject,
            assertion_text="user prefers nano",
            object_value="nano",
        )
        _backdate_invalidation(db, dead, 1)
        stats_handler = make_get_graph_stats_handler(db)
        stats = await stats_handler({})
        assert stats["entities_total"] == 2
        assert stats["entities_by_type"] == {"person": 1, "topic": 1}
        assert stats["assertions_valid"] == 1
        assert stats["assertions_invalidated"] == 1
        assert stats["orphan_entities"] == 1
        assert stats["assertions_with_missing_evidence"] == 1
        assert stats["record_queue_depth"] == 0
        assert stats["last_assertion_at"] != ""
        assert kept
        db.close()

    async def test_empty_graph_zero_stats(self, tmp_path):
        """S3.2 — empty tables produce zeros."""
        db = _open_db(tmp_path)
        stats_handler = make_get_graph_stats_handler(db)
        stats = await stats_handler({})
        assert stats["entities_total"] == 0
        assert stats["assertions_valid"] == 0
        assert stats["assertions_invalidated"] == 0
        assert stats["orphan_entities"] == 0
        assert stats["assertions_with_missing_evidence"] == 0
        assert stats["last_assertion_at"] == ""
        db.close()
