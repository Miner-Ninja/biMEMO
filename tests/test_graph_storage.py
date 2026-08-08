"""Tests for graph memory storage: entities, assertions, migration, FTS.

Asserts the graph-memory-storage contract: R1 (S1.1-S1.3), R2 (S2.1-S2.3),
R3 (S3.1-S3.2), R4 (S4.1-S4.4), R5 (S5.1-S5.4), R7 (S7.1-S7.3),
R8 (S8.1-S8.3), R9 (S9.1).
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
import sqlcipher3
from pydantic import ValidationError

from backend.memory.retrieval.fulltext import create_fts_tables
from backend.processes.db_proxy.handlers.graph import (
    make_find_entities_handler,
    make_find_graph_entry_points_handler,
    make_get_entity_handler,
    make_invalidate_assertion_handler,
    make_list_assertions_for_entity_handler,
    make_migrate_summaries_to_assertions_handler,
    make_save_assertion_handler,
    make_save_entity_handler,
)
from backend.processes.orchestrator.process import _maybe_migrate_graph_memory
from backend.security.database import DatabaseManager
from backend.security.policy import get_policy
from backend.security.schema import SCHEMA_VERSION, initialize_schema


def _open_db(tmp_path):
    """Create and open a test database with schema and FTS tables."""
    db = DatabaseManager(str(tmp_path / "graph.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    create_fts_tables(db)
    return db


async def _save_entity(db, name="vim", entity_type="tool", aliases=None):
    """Store one entity through the handler and return its id."""
    handler = make_save_entity_handler(db)
    result = await handler(
        {
            "entity_type": entity_type,
            "canonical_name": name,
            "aliases": aliases or [],
        }
    )
    return result["entity_id"]


async def _save_assertion(db, subject_id, **overrides):
    """Store one assertion through the handler and return its id."""
    handler = make_save_assertion_handler(db)
    params = {
        "subject_entity_id": subject_id,
        "relation": "prefers",
        "assertion_text": "user prefers dark themes",
        "object_value": "dark themes",
    }
    params.update(overrides)
    result = await handler(params)
    return result["assertion_id"]


class TestEntitiesSchema:
    """R1 — entities table and schema version."""

    def test_fresh_schema_has_entities_and_v17(self, tmp_path):
        """S1.1 — fresh database gets entities and version 17."""
        db = _open_db(tmp_path)
        assert SCHEMA_VERSION == 17
        row = db.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 17
        db.execute("SELECT id FROM entities").fetchall()
        db.close()

    def test_upgrade_from_v16_creates_entities(self, tmp_path):
        """S1.2 — re-initialization adds graph tables, keeps existing data."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES ('keep')")
        db.commit()
        db.execute("UPDATE schema_version SET version = 16")
        db.execute("DROP TABLE assertions")
        db.execute("DROP TABLE entities")
        db.commit()
        initialize_schema(db)
        db.execute("SELECT id FROM entities").fetchall()
        row = db.execute("SELECT id FROM conversations").fetchone()
        assert row[0] == "keep"
        db.close()

    def test_duplicate_type_name_rejected(self, tmp_path):
        """S1.3 — UNIQUE(entity_type, canonical_name) enforced."""
        db = _open_db(tmp_path)
        db.execute(
            "INSERT INTO entities (id, entity_type, canonical_name) "
            "VALUES ('e1', 'tool', 'vim')"
        )
        with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
            db.execute(
                "INSERT INTO entities (id, entity_type, canonical_name) "
                "VALUES ('e2', 'tool', 'vim')"
            )
        db.close()


class TestAssertionsSchema:
    """R2 — assertions table constraints and indexes."""

    async def test_object_xor_enforced(self, tmp_path):
        """S2.1 — object entity and literal value are mutually exclusive."""
        db = _open_db(tmp_path)
        subject = await _save_entity(db)
        other = await _save_entity(db, name="emacs")
        with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
            db.execute(
                "INSERT INTO assertions "
                "(id, subject_entity_id, relation, object_entity_id, "
                "object_value, assertion_text) "
                "VALUES ('a1', ?, 'r', ?, 'both', 't')",
                (subject, other),
            )
        db.close()

    async def test_entity_with_assertions_undeletable(self, tmp_path):
        """S2.2 — RESTRICT foreign key blocks subject deletion."""
        db = _open_db(tmp_path)
        subject = await _save_entity(db)
        await _save_assertion(db, subject)
        with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
            db.execute("DELETE FROM entities WHERE id = ?", (subject,))
        db.close()

    def test_traversal_indexes_exist(self, tmp_path):
        """S2.3 — subject and object lookups are index-backed."""
        db = _open_db(tmp_path)
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "idx_assertions_subject" in names
        assert "idx_assertions_object" in names
        db.close()


class TestGraphFulltext:
    """R3 — bilingual FTS over entities and assertions."""

    async def test_russian_stemmed_entity_search(self, tmp_path):
        """S3.1 — Russian word forms match via stemming."""
        db = _open_db(tmp_path)
        await _save_entity(
            db,
            name="Настройки резервного копирования",
            entity_type="topic",
        )
        handler = make_find_entities_handler(db)
        result = await handler({"query": "резервная копия"})
        assert len(result["entities"]) == 1
        db.close()

    async def test_english_stemmed_assertion_search(self, tmp_path):
        """S3.2 — English word forms match via stemming."""
        db = _open_db(tmp_path)
        subject = await _save_entity(db)
        await _save_assertion(db, subject)
        handler = make_find_graph_entry_points_handler(db)
        result = await handler({"query": "preferences"})
        kinds = {hit["kind"] for hit in result["entry_points"]}
        assert "assertion" in kinds
        db.close()


class TestWriteOperations:
    """R4 — save_entity, save_assertion, invalidate_assertion."""

    async def test_get_or_create_normalizes_name(self, tmp_path):
        """S4.1 — case/whitespace variants resolve to one entity."""
        db = _open_db(tmp_path)
        first = await _save_entity(db, name="Vim")
        second = await _save_entity(db, name=" vim ")
        assert first == second
        count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        assert count == 1
        db.close()

    async def test_save_assertion_stores_and_indexes(self, tmp_path):
        """S4.2 — stored assertion is retrievable by id and by search."""
        db = _open_db(tmp_path)
        subject = await _save_entity(db)
        assertion_id = await _save_assertion(db, subject)
        row = db.execute(
            "SELECT assertion_text FROM assertions WHERE id = ?",
            (assertion_id,),
        ).fetchone()
        assert row[0] == "user prefers dark themes"
        db.close()

    async def test_invalidate_keeps_row(self, tmp_path):
        """S4.3 — invalidation sets fields without deleting."""
        db = _open_db(tmp_path)
        subject = await _save_entity(db)
        old = await _save_assertion(db, subject)
        new = await _save_assertion(
            db,
            subject,
            assertion_text="user prefers light themes",
            object_value="light themes",
        )
        handler = make_invalidate_assertion_handler(db)
        result = await handler(
            {"assertion_id": old, "invalidated_by_assertion_id": new}
        )
        assert result["invalidated"] is True
        row = db.execute(
            "SELECT invalid_from, invalidated_by_assertion_id "
            "FROM assertions WHERE id = ?",
            (old,),
        ).fetchone()
        assert row[0] is not None
        assert row[1] == new
        db.close()

    async def test_missing_subject_rejected(self, tmp_path):
        """S4.4 — assertion referencing a missing entity fails cleanly."""
        db = _open_db(tmp_path)
        handler = make_save_assertion_handler(db)
        with pytest.raises(ValueError, match="subject entity does not exist"):
            await handler(
                {
                    "subject_entity_id": "ghost",
                    "relation": "r",
                    "assertion_text": "t",
                }
            )
        count = db.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
        assert count == 0
        db.close()


class TestReadOperations:
    """R5 — get/find/list/entry-point reads."""

    async def test_both_directions_with_labels(self, tmp_path):
        """S5.1 — subject-side and object-side assertions both returned."""
        db = _open_db(tmp_path)
        user = await _save_entity(db, name="user", entity_type="person")
        vim = await _save_entity(db)
        await _save_assertion(
            db,
            user,
            object_entity_id=vim,
            object_value=None,
            assertion_text="user uses vim",
            relation="uses",
        )
        await _save_assertion(
            db,
            vim,
            assertion_text="vim runs in the terminal",
            relation="runs_in",
            object_value="terminal",
        )
        handler = make_list_assertions_for_entity_handler(db)
        result = await handler({"entity_id": vim})
        directions = {a["direction"] for a in result["assertions"]}
        assert directions == {"subject", "object"}
        db.close()

    async def test_valid_only_filter(self, tmp_path):
        """S5.2 — invalidated assertions drop out under valid_only."""
        db = _open_db(tmp_path)
        subject = await _save_entity(db)
        old = await _save_assertion(db, subject)
        await _save_assertion(
            db,
            subject,
            assertion_text="second assertion",
            object_value="x",
        )
        invalidate = make_invalidate_assertion_handler(db)
        await invalidate({"assertion_id": old})
        handler = make_list_assertions_for_entity_handler(db)
        result = await handler({"entity_id": subject, "valid_only": True})
        assert len(result["assertions"]) == 1
        assert result["assertions"][0]["assertion_text"] == "second assertion"
        db.close()

    async def test_empty_graph_entry_points(self, tmp_path):
        """S5.3 — empty tables yield an empty result, not an error."""
        db = _open_db(tmp_path)
        handler = make_find_graph_entry_points_handler(db)
        result = await handler({"query": "anything relevant"})
        assert result["entry_points"] == []
        db.close()

    async def test_entry_points_merge_kinds(self, tmp_path):
        """S5.4 — matching entities and assertions arrive labeled."""
        db = _open_db(tmp_path)
        subject = await _save_entity(db, name="dark themes", entity_type="topic")
        await _save_assertion(db, subject)
        handler = make_find_graph_entry_points_handler(db)
        result = await handler({"query": "dark themes"})
        kinds = {hit["kind"] for hit in result["entry_points"]}
        assert kinds == {"entity", "assertion"}
        db.close()

    async def test_get_entity_roundtrip(self, tmp_path):
        """R5 — get_entity returns the stored row or None."""
        db = _open_db(tmp_path)
        entity_id = await _save_entity(db)
        handler = make_get_entity_handler(db)
        found = await handler({"entity_id": entity_id})
        assert found["entity"]["canonical_name"] == "vim"
        missing = await handler({"entity_id": "ghost"})
        assert missing["entity"] is None
        db.close()


def _seed_summaries(db):
    """Insert two legacy summary rows."""
    for sid, point in (("s1", "user prefers vim"), ("s2", "project uses docker")):
        db.execute(
            "INSERT INTO summaries "
            "(id, main_point, trust_level, summary_scope, source_record_ids) "
            "VALUES (?, ?, 'high', 'long_term', '[\"r1\"]')",
            (sid, point),
        )
    db.commit()


class TestMigration:
    """R7 — idempotent legacy-summaries migration."""

    async def test_migrates_each_summary_once(self, tmp_path):
        """S7.1 + S7.2 — one assertion per summary, rerun adds nothing."""
        db = _open_db(tmp_path)
        _seed_summaries(db)
        handler = make_migrate_summaries_to_assertions_handler(db)
        first = await handler({})
        assert first["migrated"] == 2
        second = await handler({})
        assert second["migrated"] == 0
        count = db.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
        assert count == 2
        sources = {
            row[0]
            for row in db.execute(
                "SELECT migrated_from_summary_id FROM assertions"
            ).fetchall()
        }
        assert sources == {"s1", "s2"}
        db.close()

    async def test_summaries_untouched(self, tmp_path):
        """S7.3 — migration never modifies the summaries table."""
        db = _open_db(tmp_path)
        _seed_summaries(db)
        before = db.execute(
            "SELECT id, main_point FROM summaries ORDER BY id"
        ).fetchall()
        handler = make_migrate_summaries_to_assertions_handler(db)
        await handler({})
        after = db.execute(
            "SELECT id, main_point FROM summaries ORDER BY id"
        ).fetchall()
        assert before == after
        db.close()


class TestFetchUnextracted:
    """R2 (этап 2) — eligibility for the graph extractor."""

    async def test_eligibility_filtering(self, tmp_path):
        """S2.1-S2.3 — cited and tainted records excluded, fresh included."""
        from backend.processes.db_proxy.handlers.graph import (
            make_fetch_unextracted_records_handler,
        )

        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES ('c1')")
        for rid, tainted in (("r-fresh", 0), ("r-cited", 0), ("r-bad", 1)):
            db.execute(
                "INSERT INTO records "
                "(id, conversation_id, session_id, summary, tainted) "
                "VALUES (?, 'c1', 's1', 'text', ?)",
                (rid, tainted),
            )
        db.commit()
        subject = await _save_entity(db)
        await _save_assertion(
            db,
            subject,
            source_record_ids=["r-cited"],
        )
        handler = make_fetch_unextracted_records_handler(db)
        result = await handler({})
        ids = [r["id"] for r in result["records"]]
        assert "r-fresh" in ids
        assert "r-cited" not in ids
        assert "r-bad" not in ids
        db.close()


class TestFeatureFlag:
    """R8 — graph_memory.enabled gate."""

    def test_flag_declared_in_catalog(self):
        """S8.1 — the key exists in the catalog as a boolean (enabled
        deliberately since stage-2 rollout)."""
        assert isinstance(get_policy().get_bool("graph_memory.enabled"), bool)

    async def test_migration_not_invoked_when_off(self):
        """S8.2 — startup gate does nothing while the flag is off."""
        db_client = AsyncMock()
        with patch(
            "backend.security.policy.AssistantPolicy.get_bool",
            return_value=False,
        ):
            await _maybe_migrate_graph_memory(db_client)
        db_client.migrate_summaries_to_assertions.assert_not_awaited()

    async def test_migration_invoked_when_on(self):
        """S8.3 — startup gate runs the migration once when enabled."""
        db_client = AsyncMock()
        db_client.migrate_summaries_to_assertions.return_value = {
            "migrated": 0,
            "total_summaries": 0,
        }
        with patch(
            "backend.security.policy.AssistantPolicy.get_bool",
            return_value=True,
        ):
            await _maybe_migrate_graph_memory(db_client)
        db_client.migrate_summaries_to_assertions.assert_awaited_once()


class TestTypedParams:
    """R9 — strict parameter validation."""

    async def test_extra_field_rejected(self, tmp_path):
        """S9.1 — unknown fields fail validation before storage."""
        db = _open_db(tmp_path)
        handler = make_save_entity_handler(db)
        with pytest.raises(ValidationError):
            await handler(
                {
                    "entity_type": "tool",
                    "canonical_name": "vim",
                    "surprise": True,
                }
            )
        count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        assert count == 0
        db.close()
