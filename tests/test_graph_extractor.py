"""Tests for the graph extractor (stage 2).

Asserts the graph-memory-extractor contract: R1 (S1.1-S1.2), R3
(S3.1-S3.4), R4 (S4.1), R5 (S5.1-S5.3), R6 (S6.2).
"""

import os

import pytest

from backend.memory.graph_extractor import (
    normalize_relation,
    parse_graph_extraction,
    process_graph_extraction,
)
from backend.memory.retrieval.fulltext import create_fts_tables
from backend.processes.db_proxy.handlers.graph import (
    make_fetch_unextracted_records_handler,
    make_find_entities_handler,
    make_invalidate_assertion_handler,
    make_list_assertions_for_entity_handler,
    make_save_assertion_handler,
    make_save_entity_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


class _GraphClient:
    """Socket-A-client stand-in routing straight to the graph handlers."""

    def __init__(self, db):
        self._save_entity = make_save_entity_handler(db)
        self._save_assertion = make_save_assertion_handler(db)
        self._invalidate = make_invalidate_assertion_handler(db)
        self._list = make_list_assertions_for_entity_handler(db)
        self._find = make_find_entities_handler(db)

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
        result = await self._invalidate(
            {
                "assertion_id": assertion_id,
                "invalidated_by_assertion_id": by_id,
            }
        )
        return result["invalidated"]

    async def list_assertions_for_entity(
        self, entity_id, valid_only=False, limit=100
    ):
        result = await self._list(
            {"entity_id": entity_id, "valid_only": valid_only, "limit": limit}
        )
        return result["assertions"]


class _FakeProvider:
    """Provider stub returning canned extraction output."""

    dialog_recorder_model = "stub"

    def __init__(self, output="", error=None):
        self._output = output
        self._error = error

    async def chat(self, *args, **kwargs):
        if self._error is not None:
            raise self._error

        class _Response:
            content = self._output

        return _Response()


def _open_db(tmp_path):
    """Create a schema-complete encrypted test database."""
    db = DatabaseManager(str(tmp_path / "extractor.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    create_fts_tables(db)
    return db


def _records():
    """One eligible record batch."""
    return [
        {
            "id": "rec-1",
            "summary": "user prefers vim",
            "key_facts": ["vim is the editor of choice"],
        }
    ]


_OUTPUT_VIM = (
    "ENTITY: person | user\n"
    'ASSERTION: user | prefers | value="vim" | user prefers vim | '
    "long_term | high\n"
)


class TestParsing:
    """R3 — line format parsing and relation vocabulary."""

    def test_parses_entities_and_assertions(self):
        """S3.1 (parse half) — declared lines become structured items."""
        extraction = parse_graph_extraction(_OUTPUT_VIM)
        assert len(extraction.entities) == 1
        assert len(extraction.assertions) == 1
        assertion = extraction.assertions[0]
        assert assertion.subject_name == "user"
        assert assertion.object_value == "vim"

    def test_unknown_relation_normalized(self):
        """S3.3 — unknown relations map to relates_to."""
        assert normalize_relation("loves") == "relates_to"
        assert normalize_relation("Prefers") == "prefers"

    def test_shifted_fields_rejected(self):
        """A scope keyword in the text slot means a field shift — reject."""
        shifted = (
            "ENTITY: person | user\n"
            'ASSERTION: user | prefers | value="vim" | short_term | high\n'
        )
        extraction = parse_graph_extraction(shifted)
        assert extraction.assertions == []

    def test_garbage_lines_discarded(self):
        """S3.4 (parse half) — unparseable output yields nothing stored."""
        extraction = parse_graph_extraction("some prose\nANOTHER: thing")
        assert extraction.entities == []
        assert extraction.assertions == []
        assert len(extraction.discarded_lines) == 2


class TestExtractionPipeline:
    """R3/R4/R5 — end-to-end extraction against real storage."""

    async def test_stores_entity_and_assertion_with_evidence(self, tmp_path):
        """S3.1 — one batch produces graph rows citing the records."""
        db = _open_db(tmp_path)
        client = _GraphClient(db)
        stored = await process_graph_extraction(
            _FakeProvider(_OUTPUT_VIM),
            client,
            _records(),
        )
        assert stored == 1
        row = db.execute(
            "SELECT source_record_ids FROM assertions",
        ).fetchone()
        assert "rec-1" in row[0]
        db.close()

    async def test_undeclared_subject_discarded(self, tmp_path):
        """S3.2 — assertion without its ENTITY line stores nothing."""
        db = _open_db(tmp_path)
        output = (
            'ASSERTION: ghost | uses | value="x" | ghost uses x | '
            "long_term | high\n"
        )
        stored = await process_graph_extraction(
            _FakeProvider(output),
            _GraphClient(db),
            _records(),
        )
        assert stored == 0
        count = db.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
        assert count == 0
        db.close()

    async def test_entity_variants_resolve_once(self, tmp_path):
        """S4.1 — case/space variants of a name yield one entity."""
        db = _open_db(tmp_path)
        client = _GraphClient(db)
        await process_graph_extraction(
            _FakeProvider(_OUTPUT_VIM), client, _records()
        )
        second = _OUTPUT_VIM.replace("| user", "|  USER ", 1).replace(
            "ENTITY: person |  USER ", "ENTITY: person |  USER "
        )
        await process_graph_extraction(
            _FakeProvider(second),
            client,
            [{"id": "rec-2", "summary": "more", "key_facts": []}],
        )
        count = db.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type = 'person'",
        ).fetchone()[0]
        assert count == 1
        db.close()

    async def test_contradiction_invalidates_old(self, tmp_path):
        """S5.1 — confident different object supersedes the old one."""
        db = _open_db(tmp_path)
        client = _GraphClient(db)
        await process_graph_extraction(
            _FakeProvider(_OUTPUT_VIM), client, _records()
        )
        emacs = _OUTPUT_VIM.replace('value="vim"', 'value="emacs"').replace(
            "user prefers vim", "user prefers emacs"
        )
        await process_graph_extraction(
            _FakeProvider(emacs),
            client,
            [{"id": "rec-2", "summary": "switch", "key_facts": []}],
        )
        rows = db.execute(
            "SELECT object_value, invalid_from, invalidated_by_assertion_id "
            "FROM assertions ORDER BY created_at",
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "vim"
        assert rows[0][1] is not None
        assert rows[0][2] is not None
        assert rows[1][1] is None
        db.close()

    async def test_exact_duplicate_not_rewritten(self, tmp_path):
        """S5.2 — identical triple creates no second row."""
        db = _open_db(tmp_path)
        client = _GraphClient(db)
        await process_graph_extraction(
            _FakeProvider(_OUTPUT_VIM), client, _records()
        )
        await process_graph_extraction(
            _FakeProvider(_OUTPUT_VIM),
            client,
            [{"id": "rec-2", "summary": "again", "key_facts": []}],
        )
        count = db.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
        assert count == 1
        db.close()

    async def test_low_trust_contradiction_keeps_both(self, tmp_path):
        """S5.3 — low-trust conflict leaves both assertions valid."""
        db = _open_db(tmp_path)
        client = _GraphClient(db)
        await process_graph_extraction(
            _FakeProvider(_OUTPUT_VIM), client, _records()
        )
        weak = (
            "ENTITY: person | user\n"
            'ASSERTION: user | prefers | value="nano" | maybe nano | '
            "long_term | low\n"
        )
        await process_graph_extraction(
            _FakeProvider(weak),
            client,
            [{"id": "rec-2", "summary": "hmm", "key_facts": []}],
        )
        valid = db.execute(
            "SELECT COUNT(*) FROM assertions WHERE invalid_from IS NULL",
        ).fetchone()[0]
        assert valid == 2
        db.close()

    async def test_model_failure_leaves_records_eligible(self, tmp_path):
        """S6.2 — a model error stores nothing; eligibility unchanged."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES ('c1')")
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary) "
            "VALUES ('rec-1', 'c1', 's1', 'user prefers vim')"
        )
        db.commit()
        with pytest.raises(RuntimeError):
            await process_graph_extraction(
                _FakeProvider(error=RuntimeError("model down")),
                _GraphClient(db),
                _records(),
            )
        fetch = make_fetch_unextracted_records_handler(db)
        result = await fetch({})
        assert [r["id"] for r in result["records"]] == ["rec-1"]
        db.close()


