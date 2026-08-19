"""Tests for graph-backed recall (stage 3).

Asserts the graph-memory-recall contract: R1 (S1.1-S1.6), R2 (S2.1),
R3 (S3.1-S3.2), R4 (S4.1-S4.3), R6 (S6.1-S6.2).
"""

import os

from backend.memory.graph_recall import (
    TRIM_MARKER,
    build_graph_recall_lines,
    build_user_fact_lines,
    load_graph_evidence_bundle,
    trim_lines_to_token_budget,
)
from backend.memory.retrieval.fulltext import create_fts_tables, index_message
from backend.processes.db_proxy.handlers.graph import (
    make_find_entities_handler,
    make_find_graph_entry_points_handler,
    make_invalidate_assertion_handler,
    make_list_assertions_for_entity_handler,
    make_save_assertion_handler,
    make_save_entity_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


class _GraphReadClient:
    """Client stand-in routing graph reads/writes to the real handlers."""

    def __init__(self, db):
        self._db = db
        self._save_entity = make_save_entity_handler(db)
        self._save_assertion = make_save_assertion_handler(db)
        self._invalidate = make_invalidate_assertion_handler(db)
        self._list = make_list_assertions_for_entity_handler(db)
        self._find_entities = make_find_entities_handler(db)
        self._entry_points = make_find_graph_entry_points_handler(db)

    async def save_entity(self, entity_type, canonical_name, aliases=None):
        result = await self._save_entity(
            {
                "entity_type": entity_type,
                "canonical_name": canonical_name,
                "aliases": aliases or [],
            }
        )
        return result["entity_id"]

    async def save_assertion(self, **kwargs):
        result = await self._save_assertion(kwargs)
        return result["assertion_id"]

    async def invalidate_assertion(self, assertion_id, by_id=None):
        await self._invalidate(
            {
                "assertion_id": assertion_id,
                "invalidated_by_assertion_id": by_id,
            }
        )

    async def list_assertions_for_entity(self, entity_id, valid_only=False, limit=100):
        result = await self._list(
            {"entity_id": entity_id, "valid_only": valid_only, "limit": limit}
        )
        return result["assertions"]

    async def find_entities(self, query, limit=20):
        result = await self._find_entities({"query": query, "limit": limit})
        return result["entities"]

    async def find_graph_entry_points(self, query, limit=20):
        result = await self._entry_points({"query": query, "limit": limit})
        return result["entry_points"]

    async def list_records(self, limit=50, offset=0):
        rows = self._db.execute(
            "SELECT id, summary, source_message_ids FROM records LIMIT ?",
            (limit,),
        ).fetchall()
        import json

        return [
            {
                "id": r[0],
                "summary": r[1],
                "source_message_ids": json.loads(r[2]),
            }
            for r in rows
        ]

    async def list_messages(self, limit=50, offset=0, conversation_id=None):
        rows = self._db.execute(
            "SELECT id, role, content FROM messages LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"id": r[0], "role": r[1], "content": r[2]} for r in rows]


def _open_db(tmp_path):
    """Create a schema-complete encrypted test database."""
    db = DatabaseManager(str(tmp_path / "recall.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    create_fts_tables(db)
    return db


async def _seed_user_fact(client, text="user prefers vim", relation="prefers"):
    """Store one valid assertion about the user entity."""
    user_id = await client.save_entity("person", "user")
    assertion_id = await client.save_assertion(
        subject_entity_id=user_id,
        relation=relation,
        assertion_text=text,
        object_value=text.split()[-1],
        trust_level="high",
        assertion_scope="long_term",
        valid_from="2026-08-01T00:00:00",
        source_record_ids=["rec-1"],
    )
    return user_id, assertion_id


class TestGraphRecallLines:
    """R1/R2 — graph section of recall output."""

    async def test_line_carries_date_trust_relation(self, tmp_path):
        """S1.1 — assertion line format."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        await _seed_user_fact(client)
        lines = await build_graph_recall_lines(client, "vim preferences")
        assert len(lines) == 1
        assert "[2026-08-01]" in lines[0]
        assert "[high]" in lines[0]
        assert "(prefers)" in lines[0]
        db.close()

    async def test_one_hop_expansion_no_duplicates(self, tmp_path):
        """S1.2 — entity hit expands into its valid assertions once."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        user_id, _ = await _seed_user_fact(client)
        await client.save_assertion(
            subject_entity_id=user_id,
            relation="uses",
            assertion_text="user uses docker",
            object_value="docker",
        )
        await client.save_assertion(
            subject_entity_id=user_id,
            relation="works_on",
            assertion_text="user works_on bimemo",
            object_value="bimemo",
        )
        lines = await build_graph_recall_lines(client, "user")
        assert len(lines) == 3
        assert len(set(lines)) == 3
        db.close()

    async def test_invalidated_assertion_hidden(self, tmp_path):
        """S1.4 — invalidated facts never surface."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        _, assertion_id = await _seed_user_fact(client)
        await client.invalidate_assertion(assertion_id)
        lines = await build_graph_recall_lines(client, "vim preferences")
        assert lines == []
        db.close()

    async def test_caps_respected(self, tmp_path):
        """S1.6 — graph section stays within its caps."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        user_id, _ = await _seed_user_fact(client)
        for index in range(12):
            await client.save_assertion(
                subject_entity_id=user_id,
                relation="states",
                assertion_text=f"user states fact number {index}",
                object_value=str(index),
            )
        lines = await build_graph_recall_lines(client, "user facts")
        assert len(lines) <= 8
        db.close()

    async def test_placeholder_entity_hidden(self, tmp_path):
        """S2.1 — the migration placeholder never appears as an entity."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        placeholder_id = await client.save_entity(
            "unresolved",
            "legacy_summaries",
        )
        await client.save_assertion(
            subject_entity_id=placeholder_id,
            relation="states",
            assertion_text="legacy fact about backups",
            object_value=None,
        )
        lines = await build_graph_recall_lines(client, "legacy summaries")
        joined = "\n".join(lines)
        assert "legacy_summaries" not in joined
        assert "legacy fact about backups" in joined
        db.close()


class TestTrimToBudget:
    """R3 — line-boundary trimming to the recall-zone budget."""

    def test_over_budget_trimmed_with_marker(self):
        """S3.1 — trimmed output fits, ends on a line, carries marker."""
        text = "\n".join(f"- line number {index} with words" for index in range(50))
        trimmed = trim_lines_to_token_budget(text, 30)
        assert trimmed.endswith(TRIM_MARKER)
        assert len(trimmed) < len(text)
        body_lines = trimmed.splitlines()
        assert body_lines[0] == "- line number 0 with words"
        assert len(body_lines) >= 2

    def test_within_budget_untouched(self):
        """S3.2 — small output passes through unchanged."""
        text = "- one line"
        assert trim_lines_to_token_budget(text, 100) == text


class TestUserFacts:
    """R4 — graph-backed long-term answer source."""

    async def test_lists_valid_user_facts(self, tmp_path):
        """S4.1 — user facts listed with dates."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        await _seed_user_fact(client)
        lines = await build_user_fact_lines(client)
        assert len(lines) == 1
        assert "user prefers vim" in lines[0]
        assert "[2026-08-01]" in lines[0]
        db.close()

    async def test_empty_graph_returns_no_lines(self, tmp_path):
        """S4.2 — no user entity → empty list (caller falls back)."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        assert await build_user_fact_lines(client) == []
        db.close()


class TestGraphEvidence:
    """R6 — assertion → records → messages evidence chain."""

    async def test_bundle_chains_to_messages(self, tmp_path):
        """S6.1 — bundle carries assertion, record, and message."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES ('c1')")
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES ('m1', 'c1', 'user', 'я предпочитаю vim')"
        )
        row = db.execute("SELECT rowid FROM messages WHERE id = 'm1'").fetchone()
        index_message(db, row[0], "я предпочитаю vim")
        db.execute(
            "INSERT INTO records "
            "(id, conversation_id, session_id, summary, source_message_ids) "
            "VALUES ('rec-1', 'c1', 's1', 'user prefers vim', '[\"m1\"]')"
        )
        db.commit()
        client = _GraphReadClient(db)
        await _seed_user_fact(client)
        bundle = await load_graph_evidence_bundle(client, "vim preferences")
        assert bundle is not None
        assert bundle.summary["main_point"] == "user prefers vim"
        assert [r["id"] for r in bundle.records] == ["rec-1"]
        assert [m["id"] for m in bundle.source_messages] == ["m1"]
        db.close()

    async def test_no_match_returns_none(self, tmp_path):
        """S6.2 — no graph match → None (caller degrades to fast path)."""
        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        assert await load_graph_evidence_bundle(client, "nothing here") is None
        db.close()


class TestRecallToolIntegration:
    """R1 — tool-level branching by flag."""

    async def test_graph_first_no_summaries_search(self, tmp_path):
        """S1.1 + S1.3 — graph lines lead; summaries not searched."""
        from backend.tools.handlers.registration import (
            _build_fast_recall_result,
        )

        db = _open_db(tmp_path)
        client = _GraphReadClient(db)
        await _seed_user_fact(client)

        searched_tables = []

        async def fulltext_search(query, table, limit):
            searched_tables.append(table)
            return []

        client.fulltext_search = fulltext_search
        result = await _build_fast_recall_result(
            db_client=client,
            query="vim preferences",
            max_message_chars=2000,
        )
        assert "user prefers vim" in result
        assert result.splitlines()[0].startswith("- [2026-08-01]")
        assert "summaries" not in searched_tables
        assert searched_tables == ["records", "messages"]
        db.close()
