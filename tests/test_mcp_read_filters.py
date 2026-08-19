"""MCP read-surface filters: tainted records and invalidated assertions.

The MCP tools recent_memory / get_memory / search_memory read straight
from the tables, so they must apply the same exclusions as the FTS and
vector paths: records with tainted=1 and assertions with invalid_from
set must never leave the database through the MCP surface.
"""

import os

import pytest

from backend.memory.retrieval.fulltext import create_fts_tables
from backend.processes.db_proxy.handlers.admin import (
    make_mcp_memory_get_handler,
    make_mcp_memory_recent_handler,
)
from backend.processes.db_proxy.handlers.graph import (
    make_find_graph_entry_points_handler,
    make_invalidate_assertion_handler,
    make_save_assertion_handler,
    make_save_entity_handler,
)
from backend.processes.db_proxy.handlers.memory import make_save_record_handler
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


pytestmark = pytest.mark.asyncio


def _open_db(tmp_path):
    """Create and open a test database with schema and FTS tables."""
    db = DatabaseManager(str(tmp_path / "mcp_filters.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    create_fts_tables(db)
    db.execute(
        "INSERT INTO conversations (id, title) VALUES (?, ?)",
        ("conv-1", "test"),
    )
    db.commit()
    return db


async def _save_record(db, summary, *, tainted=False):
    """Store one record through the handler and return its id."""
    handler = make_save_record_handler(db)
    result = await handler(
        {
            "summary": summary,
            "key_facts": [],
            "action_items": [],
            "memory_kinds": ["fact"],
            "source_message_ids": [],
            "tainted": tainted,
            "conversation_id": "conv-1",
            "session_id": "test-session",
        }
    )
    return result["record_id"]


async def _save_assertion(db, text):
    """Store one assertion (with its subject entity) and return its id."""
    entity = await make_save_entity_handler(db)(
        {"entity_type": "person", "canonical_name": "ivan", "aliases": []}
    )
    result = await make_save_assertion_handler(db)(
        {
            "subject_entity_id": entity["entity_id"],
            "relation": "prefers",
            "assertion_text": text,
            "object_value": "value",
        }
    )
    return result["assertion_id"]


async def _invalidate(db, assertion_id, by_id):
    """Invalidate one assertion in favour of another."""
    await make_invalidate_assertion_handler(db)(
        {"assertion_id": assertion_id, "invalidated_by_assertion_id": by_id}
    )


class TestRecentMemoryFilters:
    """recent_memory must hide tainted records and invalidated assertions."""

    async def test_tainted_record_hidden(self, tmp_path):
        """A tainted record does not appear in recent entries."""
        db = _open_db(tmp_path)
        clean = await _save_record(db, "clean note")
        await _save_record(db, "poisoned note", tainted=True)
        result = await make_mcp_memory_recent_handler(db)({"limit": 50, "offset": 0})
        record_ids = [
            entry["id"] for entry in result["entries"] if entry["table"] == "record"
        ]
        assert record_ids == [clean]
        db.close()

    async def test_invalidated_assertion_hidden(self, tmp_path):
        """A forgotten assertion does not appear in recent entries."""
        db = _open_db(tmp_path)
        old = await _save_assertion(db, "user prefers dark themes")
        new = await _save_assertion(db, "user prefers light themes")
        await _invalidate(db, old, new)
        result = await make_mcp_memory_recent_handler(db)({"limit": 50, "offset": 0})
        assertion_ids = [
            entry["id"] for entry in result["entries"] if entry["table"] == "assertion"
        ]
        assert assertion_ids == [new]
        db.close()


class TestGetMemoryFilters:
    """get_memory must refuse hidden entries even when the id is known."""

    async def test_tainted_record_not_retrievable(self, tmp_path):
        """Fetching a tainted record by id returns nothing."""
        db = _open_db(tmp_path)
        tainted = await _save_record(db, "poisoned note", tainted=True)
        result = await make_mcp_memory_get_handler(db)(
            {"entry_id": tainted, "table": "records"}
        )
        assert result["entry"] is None
        db.close()

    async def test_invalidated_assertion_not_retrievable(self, tmp_path):
        """Fetching a forgotten assertion by id returns nothing."""
        db = _open_db(tmp_path)
        old = await _save_assertion(db, "user prefers dark themes")
        new = await _save_assertion(db, "user prefers light themes")
        await _invalidate(db, old, new)
        result = await make_mcp_memory_get_handler(db)(
            {"entry_id": old, "table": "assertions"}
        )
        assert result["entry"] is None
        db.close()

    async def test_clean_entries_still_retrievable(self, tmp_path):
        """Untainted records and valid assertions stay accessible."""
        db = _open_db(tmp_path)
        clean = await _save_record(db, "clean note")
        valid = await _save_assertion(db, "user prefers dark themes")
        record = await make_mcp_memory_get_handler(db)(
            {"entry_id": clean, "table": "records"}
        )
        assertion = await make_mcp_memory_get_handler(db)(
            {"entry_id": valid, "table": "assertions"}
        )
        assert record["entry"] is not None
        assert assertion["entry"] is not None
        db.close()


class TestEntryPointFilters:
    """Graph entry-point search must skip invalidated assertions."""

    async def test_invalidated_assertion_not_an_entry_point(self, tmp_path):
        """Search over assertions returns only the valid one."""
        db = _open_db(tmp_path)
        old = await _save_assertion(db, "user prefers dark themes")
        new = await _save_assertion(db, "user prefers light themes")
        await _invalidate(db, old, new)
        result = await make_find_graph_entry_points_handler(db)(
            {"query": "prefers", "limit": 10}
        )
        assertion_ids = [
            hit["id"] for hit in result["entry_points"] if hit["kind"] == "assertion"
        ]
        assert assertion_ids == [new]
        db.close()
