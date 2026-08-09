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
from backend.memory.retrieval.fulltext import (
    create_fts_tables,
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
