"""Tests for the semantic fallback memory-question classifier."""

import pytest
from backend.memory.classification.fallback_classifier import (
    build_fallback_classifier_messages,
    parse_fallback_classification,
)
from backend.memory.classification.fallback_policy import (
    CONFLICTING_SIGNALS_REASON,
    UNSUPPORTED_LANGUAGE_QUESTION_REASON,
    looks_like_unknown_language_question,
    semantic_fallback_reason,
    should_use_semantic_fallback,
)
from backend.memory.classification.models import (
    ClassificationConfidence,
    MemoryQuestionIntent,
)
from backend.memory.classification.question_router import route_memory_question


class TestSemanticFallbackClassifier:
    """Tests for fallback plain-text parsing into a typed route result."""

    def test_builds_generic_fallback_messages(self):
        """Fallback messages should stay a strict two-message prompt."""
        messages = build_fallback_classifier_messages(
            "What should remain durable?",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "INTENT:" in messages[0]["content"]
        assert messages[1] == {
            "role": "user",
            "content": "What should remain durable?",
        }

    def test_parses_assistant_recall_result(self):
        """Assistant recall output should map to assistant-only memory routing."""
        result = parse_fallback_classification(
            "INTENT: assistant_recall_question\n"
            "ROLE_FILTER: assistant\n"
            "CONFIDENCE: high\n"
            "LONG_TERM_MEMORY: no\n"
            "RATIONALE: The user asks what the assistant said earlier.\n"
        )

        assert (
            result.classification.intent
            is MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION
        )
        assert result.classification.role_filter == "assistant"
        assert result.classification.memory_query is True
        assert result.classification.long_term_memory_question is False
        assert result.classification.confidence is ClassificationConfidence.HIGH

    def test_parses_long_term_memory_result(self):
        """Long-term fallback output should enable the long-term route."""
        result = parse_fallback_classification(
            "INTENT: long_term_memory_question\n"
            "ROLE_FILTER: none\n"
            "CONFIDENCE: medium\n"
            "LONG_TERM_MEMORY: yes\n"
            "RATIONALE: The user asks what should remain durable later.\n"
        )

        assert (
            result.classification.intent
            is MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION
        )
        assert result.classification.role_filter is None
        assert result.classification.memory_query is True
        assert result.classification.long_term_memory_question is True
        assert result.classification.confidence is ClassificationConfidence.MEDIUM

    def test_long_term_memory_result_discards_conflicting_role_filter(self):
        """Long-term fallback should not keep assistant or user role filters."""
        result = parse_fallback_classification(
            "INTENT: long_term_memory_question\n"
            "ROLE_FILTER: assistant\n"
            "CONFIDENCE: high\n"
            "LONG_TERM_MEMORY: yes\n"
            "RATIONALE: The user asks what should remain durable later.\n"
        )

        assert (
            result.classification.intent
            is MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION
        )
        assert result.classification.role_filter is None
        assert result.classification.long_term_memory_question is True

    def test_assistant_recall_result_discards_conflicting_long_term_flag(self):
        """Assistant recall fallback should not keep an accidental long-term flag."""
        result = parse_fallback_classification(
            "INTENT: assistant_recall_question\n"
            "ROLE_FILTER: assistant\n"
            "CONFIDENCE: high\n"
            "LONG_TERM_MEMORY: yes\n"
            "RATIONALE: The user asks what the assistant said earlier.\n"
        )

        assert (
            result.classification.intent
            is MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION
        )
        assert result.classification.role_filter == "assistant"
        assert result.classification.long_term_memory_question is False

    def test_invalid_output_falls_back_to_non_memory(self):
        """Malformed fallback output should fail safely to non-memory."""
        result = parse_fallback_classification(
            "INTENT: something-unknown\n"
            "ROLE_FILTER: none\n"
            "CONFIDENCE: unknown\n"
            "LONG_TERM_MEMORY: no\n"
            "RATIONALE: unclear\n"
        )

        assert result.classification.intent is MemoryQuestionIntent.NON_MEMORY_QUESTION
        assert result.classification.memory_query is False
        assert result.classification.long_term_memory_question is False
        assert result.classification.confidence is ClassificationConfidence.LOW

    def test_unknown_language_question_shape_can_trigger_fallback(self):
        """Unsupported-language questions should still qualify for fallback."""
        assert (
            looks_like_unknown_language_question(
                "Si vuelvo mañana, qué estilo de respuesta debes recordar?",
            )
            is True
        )

    def test_non_memory_unknown_language_question_can_use_fallback(self):
        """Unknown-language question shape should widen the fallback entrypoint."""
        route = route_memory_question(
            "Si vuelvo mañana, qué estilo de respuesta debes recordar?",
        )

        assert route.intent is MemoryQuestionIntent.NON_MEMORY_QUESTION
        assert (
            should_use_semantic_fallback(
                route,
                "Si vuelvo mañana, qué estilo de respuesta debes recordar?",
            )
            is True
        )
        assert (
            semantic_fallback_reason(
                route,
                "Si vuelvo mañana, qué estilo de respuesta debes recordar?",
            )
            == UNSUPPORTED_LANGUAGE_QUESTION_REASON
        )

    @pytest.mark.parametrize(
        "question",
        [
            "Can you write a Python function example?",
            "Можешь написать пример функции на Python?",
            "Ar gali parašyti Python funkcijos pavyzdį?",
            "Puedes escribir un ejemplo de función en Python?",
        ],
    )
    def test_supported_language_non_memory_questions_do_not_use_unsupported_fallback(
        self,
        question: str,
    ):
        """Supported-language question shapes should not trigger fallback alone."""
        route = route_memory_question(question)

        assert route.intent is MemoryQuestionIntent.NON_MEMORY_QUESTION
        assert looks_like_unknown_language_question(question) is False
        assert should_use_semantic_fallback(route, question) is False
        assert semantic_fallback_reason(route, question) is None

    @pytest.mark.parametrize(
        "question",
        [
            "Si vuelvo mañana, qué estilo de respuesta debes recordar?",
            "Was hast du mir früher über Ollama gesagt?",
            "Když se zítra vrátím, jaký styl odpovědí si máš pamatovat?",
        ],
    )
    def test_compact_unsupported_language_questions_share_one_fallback_reason(
        self,
        question: str,
    ):
        """Unsupported-language prompts should use one stable fallback reason."""
        route = route_memory_question(question)

        assert route.intent is MemoryQuestionIntent.NON_MEMORY_QUESTION
        assert should_use_semantic_fallback(route, question) is True
        assert semantic_fallback_reason(route, question) == (
            UNSUPPORTED_LANGUAGE_QUESTION_REASON
        )

    def test_conflicting_signal_route_reports_its_fallback_reason(self):
        """Conflicting direct signals should surface a stable fallback reason."""
        route = route_memory_question(
            "Is this only temporary context or an active preference?",
        )

        assert (
            semantic_fallback_reason(
                route,
                "Is this only temporary context or an active preference?",
            )
            == CONFLICTING_SIGNALS_REASON
        )
