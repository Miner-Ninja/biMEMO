"""Tests for tainted session record flagging.

Covers marking records as tainted, filtering
tainted records from context and Summary Distiller.
"""

import os

from backend.memory.retrieval.fulltext import create_fts_tables, index_observation
from backend.memory.storage.tainted import (
    fetch_untainted_for_distiller,
    fetch_untainted_records,
)
from backend.processes.db_proxy.handlers.message import make_fulltext_search_handler
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


def _open_db(tmp_path):
    """Create and open a test database with schema."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    key = os.urandom(32)
    db.open(key)
    initialize_schema(db)
    return db


def _seed_observations(db):
    """Insert test conversation and records."""
    db.execute(
        "INSERT INTO conversations (id) VALUES (?)",
        ("c1",),
    )
    db.execute(
        "INSERT INTO records "
        "(id, conversation_id, session_id, "
        "summary, key_facts, tainted) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("o1", "c1", "s1", "clean obs", "[]", 0),
    )
    db.execute(
        "INSERT INTO records "
        "(id, conversation_id, session_id, "
        "summary, key_facts, tainted) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("o2", "c1", "s1", "tainted obs", "[]", 1),
    )
    db.execute(
        "INSERT INTO records "
        "(id, conversation_id, session_id, "
        "summary, key_facts, tainted) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("o3", "c1", "s2", "other session", "[]", 0),
    )
    db.commit()


class TestSearchExcludesTainted:
    """Tainted records must never surface via full-text search."""

    async def test_fulltext_search_filters_tainted(self, tmp_path):
        """A tainted record stays hidden even when it sits in the FTS index."""
        db = _open_db(tmp_path)
        create_fts_tables(db)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        seeded = [
            ("clean-1", "user preference for dark themes", 0),
            ("dirty-1", "injected preference payload", 1),
        ]
        for record_id, summary, tainted in seeded:
            db.execute(
                "INSERT INTO records "
                "(id, conversation_id, session_id, summary, key_facts, tainted) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (record_id, "c1", "s1", summary, "[]", tainted),
            )
            row = db.execute(
                "SELECT rowid FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()
            index_observation(db, row[0], summary, "[]")
        db.commit()

        handler = make_fulltext_search_handler(db)
        result = await handler({"query": "preference", "table": "records"})

        found_ids = {r["id"] for r in result["results"]}
        assert "clean-1" in found_ids
        assert "dirty-1" not in found_ids
        db.close()


class TestFetchUntainted:
    """Tests for untainted record filtering."""

    def test_excludes_tainted_other_session(self, tmp_path):
        """Verify tainted obs excluded from other sessions."""
        db = _open_db(tmp_path)
        _seed_observations(db)
        rows = fetch_untainted_records(db, session_id="s2")
        ids = [r[0] for r in rows]
        assert "o1" in ids
        assert "o2" not in ids  # tainted from s1, excluded
        assert "o3" in ids  # same session s2, included
        db.close()

    def test_includes_tainted_same_session(self, tmp_path):
        """Verify tainted obs included in same session."""
        db = _open_db(tmp_path)
        _seed_observations(db)
        rows = fetch_untainted_records(db, session_id="s1")
        ids = [r[0] for r in rows]
        assert "o2" in ids
        db.close()

    def test_no_session_excludes_all_tainted(self, tmp_path):
        """Verify no session excludes all tainted."""
        db = _open_db(tmp_path)
        _seed_observations(db)
        rows = fetch_untainted_records(db)
        ids = [r[0] for r in rows]
        assert "o2" not in ids
        assert "o1" in ids
        assert "o3" in ids
        db.close()


class TestFetchForDistiller:
    """Tests for Summary Distiller record filtering."""

    def test_excludes_all_tainted(self, tmp_path):
        """Verify all tainted excluded from Summary Distiller."""
        db = _open_db(tmp_path)
        _seed_observations(db)
        rows = fetch_untainted_for_distiller(db)
        ids = [r[0] for r in rows]
        assert "o2" not in ids
        assert "o1" in ids
        assert "o3" in ids
        db.close()

    def test_respects_limit(self, tmp_path):
        """Verify limit is respected."""
        db = _open_db(tmp_path)
        _seed_observations(db)
        rows = fetch_untainted_for_distiller(db, limit=1)
        assert len(rows) == 1
        db.close()

    def test_excludes_already_reflected_observations(self, tmp_path):
        """Verify records already covered by a summary are skipped."""
        db = _open_db(tmp_path)
        _seed_observations(db)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("r1", "pattern", "[]", "high", 3, '["o1"]'),
        )
        db.commit()

        rows = fetch_untainted_for_distiller(db)
        ids = [row[0] for row in rows]

        assert "o1" not in ids
        assert "o3" in ids
        db.close()
