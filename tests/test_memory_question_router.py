"""Tests for memory question routing."""

from backend.memory.classification.models import (
    ClassificationConfidence,
    MemoryQuestionIntent,
)
from backend.memory.classification.question_router import route_memory_question


class TestMemoryQuestionRouter:
    """Tests for question-to-route classification."""

    def test_routes_long_term_memory_question(self):
        """Long-term memory questions should enable the long-term route."""
        route = route_memory_question(
            "If I return tomorrow, what answer style should you still remember?",
        )

        assert route.memory_query is True
        assert route.long_term_memory_question is True
        assert route.role_filter is None
        assert route.intent is MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION
        assert route.confidence is ClassificationConfidence.HIGH
        assert route.fallback_recommended is False
        assert route.source == "signal"
        assert "long_term_memory_signals" in route.matched_signal_groups
        assert route.fallback_rationale is None

    def test_routes_assistant_memory_question(self):
        """Assistant memory questions should request assistant-only search."""
        route = route_memory_question("Ты мне говорил про настройки Ollama?")

        assert route.memory_query is True
        assert route.long_term_memory_question is False
        assert route.role_filter == "assistant"
        assert route.intent is MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION
        assert route.confidence is ClassificationConfidence.HIGH
        assert route.source == "signal"
        assert "assistant_recall_signals" in route.matched_signal_groups

    def test_routes_non_memory_question(self):
        """Non-memory questions should stay on the normal path."""
        route = route_memory_question("Напиши пример функции на Python")

        assert route.memory_query is False
        assert route.long_term_memory_question is False
        assert route.role_filter is None
        assert route.intent is MemoryQuestionIntent.NON_MEMORY_QUESTION
        assert route.confidence is ClassificationConfidence.NONE
        assert route.source == "signal"
        assert route.matched_signal_groups == ()

    def test_routes_english_assistant_memory_question(self):
        """English assistant recall should use the same route."""
        route = route_memory_question("What did you tell me about Ollama earlier?")

        assert route.memory_query is True
        assert route.long_term_memory_question is False
        assert route.role_filter == "assistant"
        assert route.intent is MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION
        assert route.source == "signal"

    def test_routes_english_user_memory_question(self):
        """English user recall should use the same route."""
        route = route_memory_question("What did I tell you about my timezone?")

        assert route.memory_query is True
        assert route.long_term_memory_question is False
        assert route.role_filter == "user"
        assert route.intent is MemoryQuestionIntent.USER_RECALL_QUESTION
        assert route.source == "signal"

    def test_routes_russian_long_term_memory_question(self):
        """Russian long-term memory prompts should use the long-term route."""
        route = route_memory_question(
            "Если я вернусь завтра, какой стиль ответа ты должен помнить?",
        )

        assert route.memory_query is True
        assert route.long_term_memory_question is True
        assert route.role_filter is None
        assert route.intent is MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION
        assert route.source == "signal"

    def test_routes_lithuanian_assistant_memory_question(self):
        """Lithuanian assistant recall should use assistant-only search."""
        route = route_memory_question("Tu man sakei apie Ollama nustatymus?")

        assert route.memory_query is True
        assert route.long_term_memory_question is False
        assert route.role_filter == "assistant"

    def test_marks_ambiguous_question_for_fallback(self):
        """Mixed temporary-versus-durable wording should request fallback."""
        route = route_memory_question(
            "Is this only temporary context or an active preference?",
        )

        assert route.memory_query is True
        assert route.long_term_memory_question is False
        assert route.intent is MemoryQuestionIntent.MEMORY_RECALL_QUESTION
        assert route.confidence is ClassificationConfidence.LOW
        assert route.fallback_recommended is True
        assert route.source == "signal"
        assert route.matched_signal_groups == (
            "temporary_context_signals",
            "durable_preference_signals",
        )
