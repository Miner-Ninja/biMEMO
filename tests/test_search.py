"""Tests for hybrid search with Reciprocal Rank Fusion.

Covers RRF scoring, source tagging (vector/keyword/hybrid),
sort order, deduplication, and limit enforcement.
"""

import os

from backend.memory.classification.question_classifier import (
    classify_memory_question,
    detect_message_role_filter,
    is_memory_query,
    is_persistence_question,
)
from backend.memory.retrieval import (
    _resolve_id,
    _rrf_score,
    hybrid_search,
)
from backend.memory.retrieval.fulltext import (
    create_fts_tables,
    index_message,
    index_observation,
    index_reflection,
)
from backend.memory.retrieval.vector_store import (
    create_vector_table,
    enable_vector_extension,
    store_embedding,
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
    enable_vector_extension(db)
    create_vector_table(db, 4)
    return db


class TestRRFScore:
    """Tests for Reciprocal Rank Fusion score computation."""

    def test_rank_1_default_k(self):
        """Verify RRF score for rank 1 with k=60."""
        assert _rrf_score(1) == 1.0 / 61

    def test_rank_2_default_k(self):
        """Verify RRF score for rank 2 with k=60."""
        assert _rrf_score(2) == 1.0 / 62

    def test_higher_rank_lower_score(self):
        """Verify higher rank position yields lower score."""
        assert _rrf_score(1) > _rrf_score(2) > _rrf_score(10)

    def test_custom_k(self):
        """Verify custom k parameter works."""
        assert _rrf_score(1, k=0) == 1.0


class TestResolveId:
    """Tests for FTS rowid to string ID resolution."""

    def test_unknown_table_returns_none(self, tmp_path):
        """Verify unknown table name returns None."""
        db = _open_db(tmp_path)
        assert _resolve_id(db, "unknown_table", 1) is None
        db.close()

    def test_summary_rowid_resolves_to_string_id(self, tmp_path):
        """Verify summary rowid resolves to its string ID."""
        db = _open_db(tmp_path)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("r1", "Pattern", '["e1"]', "high", 3, '["o1"]'),
        )
        db.commit()
        index_reflection(db, 1, "Pattern", '["e1"]')
        assert _resolve_id(db, "summaries", 1) == "r1"
        db.close()


class TestMemoryQueryDetection:
    """Tests for memory-seeking query detection."""

    def test_detects_prior_assistant_statement(self):
        """Queries about prior assistant answers should count as memory-seeking."""
        assert is_memory_query("Ты мне говорил про настройки Ollama?") is True

    def test_detects_prior_conversation_reference(self):
        """Queries about earlier discussion should count as memory-seeking."""
        assert is_memory_query("\u041e чем мы говорили вчера про память?") is True

    def test_detects_durable_preference_meta_question(self):
        """Durable-preference recall prompts should use memory-query mode."""
        assert (
            is_memory_query(
                "What part of this conversation should remain durable "
                "preference memory?",
            )
            is True
        )

    def test_detects_final_long_term_preference_question(self):
        """Final long-term preference prompts should use memory-query mode."""
        assert (
            is_memory_query(
                "State the final active long-term preference and the temporary "
                "short-term context.",
            )
            is True
        )

    def test_regular_question_is_not_memory_query(self):
        """A normal factual request should not trigger memory-query mode."""
        assert is_memory_query("Напиши пример функции на Python") is False

    def test_detects_persistence_question(self):
        """Persistence questions should be detected for the structured mode."""
        assert (
            is_persistence_question(
                "If I return tomorrow, what answer style should you still remember?",
            )
            is True
        )

    def test_non_persistence_memory_query_is_not_persistence_question(self):
        """General memory recall should not enter the persistence-only mode."""
        assert is_persistence_question("Ты мне говорил про настройки Ollama?") is False

    def test_detects_long_term_summary_prompt(self):
        """Long-term summary prompts should use the structured persistence mode."""
        assert (
            is_persistence_question(
                "Summarize my long-term preference in one sentence.",
            )
            is True
        )

    def test_detects_future_replies_prompt(self):
        """Future-replies prompts should use the structured persistence mode."""
        assert (
            is_persistence_question(
                "What should you remember about my current answer style in "
                "future replies?",
            )
            is True
        )

    def test_detects_english_assistant_recall(self):
        """English recall should trigger memory-query mode and assistant filter."""
        classification = classify_memory_question(
            "What did you tell me about Ollama earlier?",
        )

        assert classification.memory_query is True
        assert classification.role_filter == "assistant"

    def test_detects_english_user_recall(self):
        """English user-recall should trigger memory-query mode and user filter."""
        classification = classify_memory_question(
            "What did I tell you about my timezone earlier?",
        )

        assert classification.memory_query is True
        assert classification.role_filter == "user"

    def test_detects_russian_long_term_question(self):
        """Russian durable-memory phrasing should use the long-term mode."""
        assert (
            is_persistence_question(
                "Если я вернусь завтра, какой стиль ответа ты должен помнить?",
            )
            is True
        )

    def test_detects_lithuanian_long_term_question(self):
        """Lithuanian durable-memory phrasing should use the long-term mode."""
        assert (
            is_persistence_question(
                "Jei grįžčiau rytoj, kokį atsakymų stilių turėtum prisiminti?",
            )
            is True
        )


class TestHybridSearch:
    """Tests for combined vector + keyword search with RRF."""

    def test_hybrid_source_when_found_by_both(self, tmp_path):
        """Verify source='hybrid' when doc found by vector and keyword."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO records "
            "(id, conversation_id, session_id, summary, key_facts) "
            "VALUES (?, ?, ?, ?, ?)",
            ("o1", "c1", "s1", "python programming", "[]"),
        )
        db.commit()
        index_observation(db, 1, "python programming", "[]")
        store_embedding(db, "o1", [1.0, 0.0, 0.0, 0.0])

        results = hybrid_search(
            db,
            "python",
            [1.0, 0.0, 0.0, 0.0],
            "records",
            limit=10,
        )
        assert len(results) == 1
        assert results[0].identifier == "o1"
        assert results[0].source == "hybrid"
        assert results[0].score > _rrf_score(1)  # got contributions from both
        db.close()

    def test_rrf_scores_are_positive_and_sorted_descending(self, tmp_path):
        """Verify all scores are positive and sorted descending."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        for i in range(3):
            db.execute(
                "INSERT INTO records "
                "(id, conversation_id, session_id, summary, key_facts) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"o{i}", "c1", "s1", f"topic{i} content", "[]"),
            )
            index_observation(db, i + 1, f"topic{i} content", "[]")
            vec = [0.0] * 4
            vec[i % 4] = 1.0
            store_embedding(db, f"o{i}", vec)
        db.commit()

        results = hybrid_search(
            db,
            "content",
            [1.0, 0.0, 0.0, 0.0],
            "records",
            limit=10,
        )
        assert len(results) >= 1
        for r in results:
            assert r.score > 0
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
        db.close()

    def test_keyword_only_source(self, tmp_path):
        """Verify source='keyword' when only FTS matches."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            ("m1", "c1", "user", "findable keyword"),
        )
        db.commit()
        index_message(db, 1, "findable keyword")

        results = hybrid_search(
            db,
            "findable",
            None,
            "messages",
            limit=10,
        )
        assert len(results) >= 1
        assert results[0].source == "keyword"
        db.close()

    def test_vector_only_source(self, tmp_path):
        """Verify source='vector' when only vector matches."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO records "
            "(id, conversation_id, session_id, summary, key_facts) "
            "VALUES (?, ?, ?, ?, ?)",
            ("o1", "c1", "s1", "unrelated summary", "[]"),
        )
        db.commit()
        store_embedding(db, "o1", [1.0, 0.0, 0.0, 0.0])

        results = hybrid_search(
            db,
            "nonexistent_query_xyz",
            [1.0, 0.0, 0.0, 0.0],
            "records",
            limit=10,
        )
        vector_results = [r for r in results if r.source == "vector"]
        assert len(vector_results) >= 1
        db.close()

    def test_deduplicates_results(self, tmp_path):
        """Verify documents found by both sources appear once as 'hybrid'."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO records "
            "(id, conversation_id, session_id, summary, key_facts) "
            "VALUES (?, ?, ?, ?, ?)",
            ("o1", "c1", "s1", "unique searchterm", "[]"),
        )
        db.commit()
        index_observation(db, 1, "unique searchterm", "[]")
        store_embedding(db, "o1", [1.0, 0.0, 0.0, 0.0])

        results = hybrid_search(
            db,
            "searchterm",
            [1.0, 0.0, 0.0, 0.0],
            "records",
            limit=10,
        )
        ids = [r.identifier for r in results]
        assert ids.count("o1") == 1
        db.close()

    def test_respects_limit(self, tmp_path):
        """Verify results are limited."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        for i in range(5):
            db.execute(
                "INSERT INTO records "
                "(id, conversation_id, session_id, summary, key_facts) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"o{i}", "c1", "s1", f"msg {i}", "[]"),
            )
            index_observation(db, i + 1, f"msg {i}", "[]")
            vec = [0.0] * 4
            vec[i % 4] = 1.0
            store_embedding(db, f"o{i}", vec)
        db.commit()

        results = hybrid_search(
            db,
            "msg",
            [1.0, 0.0, 0.0, 0.0],
            "records",
            limit=2,
        )
        assert len(results) <= 2
        db.close()

    def test_no_embedding_skips_vector(self, tmp_path):
        """Verify None embedding skips vector search gracefully."""
        db = _open_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            ("m1", "c1", "user", "keyword only test"),
        )
        db.commit()
        index_message(db, 1, "keyword only test")

        results = hybrid_search(
            db,
            "keyword",
            None,
            "messages",
            limit=10,
        )
        assert len(results) >= 1
        assert all(r.source == "keyword" for r in results)
        db.close()

    def test_summary_keyword_search(self, tmp_path):
        """Verify summaries participate in keyword-only search."""
        db = _open_db(tmp_path)
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "r1",
                "User prefers Russian responses",
                '["Asked for Russian output repeatedly"]',
                "high",
                3,
                '["o1"]',
            ),
        )
        db.commit()
        index_reflection(
            db,
            1,
            "User prefers Russian responses",
            '["Asked for Russian output repeatedly"]',
        )

        results = hybrid_search(
            db,
            "Russian",
            None,
            "summaries",
            limit=10,
        )
        assert len(results) == 1
        assert results[0].identifier == "r1"
        assert results[0].source == "keyword"
        db.close()


class TestDetectMessageRoleFilter:
    """Tests for intent-based message role detection."""

    def test_assistant_role_ty_mne_govoril(self):
        """'ты мне говорил про Python' → assistant."""
        assert detect_message_role_filter("ты мне говорил про Python") == "assistant"

    def test_assistant_role_ty_nahodil(self):
        """'ты находил ошибку' → assistant."""
        assert detect_message_role_filter("ты находил ошибку") == "assistant"

    def test_user_role_ya_tebe_govoril(self):
        """First-person past-tense statement should match the user role."""
        assert (
            detect_message_role_filter(
                "\u044f \u0442\u0435\u0431\u0435 \u0433\u043e\u0432\u043e\u0440\u0438"
                "\u043b \u043e \u043f\u0440\u043e\u0435\u043a\u0442\u0435",
            )
            == "user"
        )

    def test_user_role_ya_soobschal(self):
        """Another first-person report should also match the user role."""
        assert (
            detect_message_role_filter(
                "\u044f \u0441\u043e\u043e\u0431\u0449\u0430\u043b \u043e \u043f\u0440"
                "\u043e\u0431\u043b\u0435\u043c\u0435",
            )
            == "user"
        )

    def test_no_filter_neutral(self):
        """Neutral query returns None."""
        assert detect_message_role_filter("Какую кухню я люблю?") is None

    def test_no_filter_empty(self):
        """Empty string returns None."""
        assert detect_message_role_filter("") is None

    def test_case_insensitive(self):
        """Case-insensitive matching works."""
        assert detect_message_role_filter("Ты мне Говорил") == "assistant"
