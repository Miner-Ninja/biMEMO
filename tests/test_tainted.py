"""Tests for tainted-record isolation: search must never surface them."""

import os

from backend.memory.retrieval.fulltext import create_fts_tables, index_observation
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


class TestSummariesTableGone:
    """S6.3 — the summaries search table no longer exists."""

    async def test_summaries_search_rejected(self, tmp_path):
        """Asking full-text search for summaries is an explicit error."""
        import pytest

        db = _open_db(tmp_path)
        create_fts_tables(db)
        handler = make_fulltext_search_handler(db)
        with pytest.raises(ValueError, match="Invalid search table"):
            await handler({"query": "anything", "table": "summaries"})
        db.close()
