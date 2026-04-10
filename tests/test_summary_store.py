"""Tests for summary storage operations.

Covers listing, detail retrieval, deletion, and
counting of summaries.
"""

import json
import os

from backend.memory.retrieval.fulltext import (
    create_fts_tables,
    index_reflection,
    search_reflections,
)
from backend.memory.summary_distiller.store import (
    append_summary_sources,
    count_distillations,
    delete_distillation,
    get_distillation,
    list_revalidated_distillations,
    list_summaries,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


def _open_db(tmp_path):
    """Create and open a test database with schema."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    key = os.urandom(32)
    db.open(key)
    initialize_schema(db)
    create_fts_tables(db)
    return db


def _seed_distillations(db):
    """Insert test summaries."""
    db.execute(
        "INSERT INTO summaries "
        "("
        "id, main_point, based_on, trust_level, summary_scope, "
        "trust_level_score, source_record_ids"
        ") "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "Pattern A", '["e1"]', "high", "long_term", 3, '["o1"]'),
    )
    db.execute(
        "INSERT INTO summaries "
        "("
        "id, main_point, based_on, trust_level, summary_scope, "
        "trust_level_score, source_record_ids"
        ") "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r2", "Pattern B", '["e2"]', "medium", "short_term", 2, '["o2"]'),
    )
    db.commit()


def _seed_observation(
    db,
    record_id: str,
    tainted: int = 0,
) -> None:
    """Insert a linked record used by summary tests."""
    db.execute("INSERT OR IGNORE INTO conversations (id) VALUES (?)", ("c1",))
    db.execute(
        "INSERT INTO records "
        "(id, conversation_id, session_id, summary, key_facts, action_items, tainted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            record_id,
            "c1",
            "s1",
            f"summary-{record_id}",
            "[]",
            "[]",
            tainted,
        ),
    )
    db.commit()


class TestListSummaries:
    """Tests for listing summaries."""

    def test_lists_all(self, tmp_path):
        """Verify all summaries are listed."""
        db = _open_db(tmp_path)
        _seed_distillations(db)
        rows = list_summaries(db)
        assert len(rows) == 2
        db.close()

    def test_revalidated_distillations_expose_scope(self, tmp_path):
        """Stored summary scope should be returned in admin records."""
        db = _open_db(tmp_path)
        _seed_distillations(db)
        rows = list_revalidated_distillations(db)
        assert rows[0]["scope"] in {"long_term", "short_term"}
        assert rows[1]["scope"] in {"long_term", "short_term"}
        db.close()

    def test_respects_limit(self, tmp_path):
        """Verify limit is respected."""
        db = _open_db(tmp_path)
        _seed_distillations(db)
        rows = list_summaries(db, limit=1)
        assert len(rows) == 1
        db.close()

    def test_respects_offset(self, tmp_path):
        """Verify offset skips rows."""
        db = _open_db(tmp_path)
        _seed_distillations(db)
        rows = list_summaries(db, limit=10, offset=1)
        assert len(rows) == 1
        db.close()

    def test_empty_table(self, tmp_path):
        """Verify empty table returns empty list."""
        db = _open_db(tmp_path)
        rows = list_summaries(db)
        assert len(rows) == 0
        db.close()

    def test_revalidated_distillations_penalize_orphaned_support(self, tmp_path):
        """Orphaned summaries should surface with degraded effective score."""
        db = _open_db(tmp_path)
        _seed_observation(db, "o1", tainted=0)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids, created_at"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "fresh",
                "Fresh supported pattern",
                '["e1"]',
                "medium",
                2,
                '["o1"]',
                "2026-03-10T12:00:00",
            ),
        )
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids, created_at"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "orphaned",
                "Old unsupported main point",
                '["e2"]',
                "high",
                3,
                '["missing-record"]',
                "2025-01-01T12:00:00",
            ),
        )
        db.commit()

        rows = list_revalidated_distillations(db)
        assert rows[0]["id"] == "fresh"
        assert rows[0]["revalidation_status"] == "fresh"
        assert rows[1]["id"] == "orphaned"
        assert rows[1]["revalidation_status"] == "orphaned"
        assert rows[1]["effective_trust_level_score"] == 1
        db.close()

    def test_append_summary_sources_refreshes_duplicate_content(self, tmp_path):
        """Merged duplicate summaries should keep richer text and reasons."""
        db = _open_db(tmp_path)
        _seed_observation(db, "o1", tainted=0)
        _seed_observation(db, "o2", tainted=0)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids, record_relations"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "r-merge",
                "User prefers detailed answers.",
                '["Detailed answers are active"]',
                "medium",
                2,
                '["o1"]',
                '[{"record_id": "o1", "relation": "supports"}]',
            ),
        )
        db.commit()

        updated = append_summary_sources(
            db,
            "r-merge",
            ["o2"],
            [{"record_id": "o2", "relation": "supports"}],
            main_point=(
                "User prefers detailed step-by-step answers with context and "
                "reasoning for future technical help."
            ),
            based_on=[
                "Detailed answers are active",
                "Future technical answers must include context and reasoning",
            ],
            trust_level="high",
            trust_level_score=3,
        )

        assert updated is True
        row = db.execute(
            "SELECT main_point, based_on, trust_level, summary_scope, "
            "trust_level_score, source_record_ids, record_relations "
            "FROM summaries WHERE id = ?",
            ("r-merge",),
        ).fetchone()
        assert row is not None
        assert row[0] == (
            "User prefers detailed step-by-step answers with context and "
            "reasoning for future technical help."
        )
        assert json.loads(row[1]) == [
            "Detailed answers are active",
            "Future technical answers must include context and reasoning",
        ]
        assert row[2] == "high"
        assert row[3] == "long_term"
        assert row[4] == 3
        assert json.loads(row[5]) == ["o1", "o2"]
        assert json.loads(row[6]) == [
            {"record_id": "o1", "relation": "supports"},
            {"record_id": "o2", "relation": "supports"},
        ]
        db.close()

    def test_revalidated_distillations_build_default_support_relations(self, tmp_path):
        """Rows without relations should expose typed support relations by default."""
        db = _open_db(tmp_path)
        _seed_observation(db, "o1", tainted=0)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "r-link",
                "Supported main point",
                '["e1"]',
                "medium",
                2,
                '["o1"]',
            ),
        )
        db.commit()

        rows = list_revalidated_distillations(db)
        assert rows[0]["record_relations"] == [
            {"record_id": "o1", "relation": "supports"}
        ]
        db.close()

    def test_revalidated_distillations_preserve_current_relation_names(self, tmp_path):
        """Stored relation names should remain unchanged in the current vocabulary."""
        db = _open_db(tmp_path)
        _seed_observation(db, "o1", tainted=0)
        _seed_observation(db, "o2", tainted=0)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids, record_relations"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "r-current-relations",
                "Current relation main point",
                '["e1"]',
                "medium",
                2,
                '["o1", "o2"]',
                (
                    '[{"record_id": "o1", "relation": "conflicts"}, '
                    '{"record_id": "o2", "relation": "outdates"}]'
                ),
            ),
        )
        db.commit()

        rows = list_revalidated_distillations(db)
        assert rows[0]["record_relations"] == [
            {"record_id": "o1", "relation": "conflicts"},
            {"record_id": "o2", "relation": "outdates"},
        ]
        db.close()

    def test_revalidated_distillations_mark_outdated_relations(self, tmp_path):
        """Active outdating relations should downgrade the summary."""
        db = _open_db(tmp_path)
        _seed_observation(db, "o1", tainted=0)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids, record_relations"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "r-outdated",
                "Old preference",
                '["reason"]',
                "high",
                3,
                '["o1"]',
                '[{"record_id": "o1", "relation": "outdates"}]',
            ),
        )
        db.commit()

        rows = list_revalidated_distillations(db)
        assert rows[0]["revalidation_status"] == "outdated"
        assert rows[0]["active_outdate_count"] == 1
        assert rows[0]["effective_trust_level_score"] == 1
        db.close()

    def test_revalidated_distillations_mark_conflicted_relations(self, tmp_path):
        """Active conflicting relations should mark the summary as conflicted."""
        db = _open_db(tmp_path)
        _seed_observation(db, "o1", tainted=0)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids, record_relations"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "r-conflicted",
                "Conflicted preference",
                '["reason"]',
                "high",
                3,
                '["o1"]',
                '[{"record_id": "o1", "relation": "conflicts"}]',
            ),
        )
        db.commit()

        rows = list_revalidated_distillations(db)
        assert rows[0]["revalidation_status"] == "conflicted"
        assert rows[0]["active_conflict_count"] == 1
        assert rows[0]["effective_trust_level_score"] == 1
        db.close()


class TestGetSummary:
    """Tests for retrieving a single summary."""

    def test_found(self, tmp_path):
        """Verify summary is found by ID."""
        db = _open_db(tmp_path)
        _seed_distillations(db)
        row = get_distillation(db, "r1")
        assert row is not None
        assert row[1] == "Pattern A"
        assert row[4] == "long_term"
        db.close()

    def test_not_found(self, tmp_path):
        """Verify None returned for missing ID."""
        db = _open_db(tmp_path)
        row = get_distillation(db, "nonexistent")
        assert row is None
        db.close()


class TestDeleteSummary:
    """Tests for deleting summaries."""

    def test_delete_existing(self, tmp_path):
        """Verify existing summary is deleted."""
        db = _open_db(tmp_path)
        _seed_distillations(db)
        index_reflection(db, 1, "Alpha summary marker", '["e1"]')
        index_reflection(db, 2, "Pattern B", '["e2"]')
        db.commit()
        result = delete_distillation(db, "r1")
        assert result is True
        assert get_distillation(db, "r1") is None
        assert search_reflections(db, "Alpha summary", 10) == []
        db.close()

    def test_delete_nonexistent(self, tmp_path):
        """Verify deleting nonexistent returns False."""
        db = _open_db(tmp_path)
        result = delete_distillation(db, "nonexistent")
        assert result is False
        db.close()


class TestCountSummaries:
    """Tests for counting summaries."""

    def test_count(self, tmp_path):
        """Verify correct count returned."""
        db = _open_db(tmp_path)
        _seed_distillations(db)
        assert count_distillations(db) == 2
        db.close()

    def test_count_empty(self, tmp_path):
        """Verify zero count for empty table."""
        db = _open_db(tmp_path)
        assert count_distillations(db) == 0
        db.close()
