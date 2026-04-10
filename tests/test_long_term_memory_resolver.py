"""Tests for the deterministic long-term memory resolver."""

from backend.memory.classification.resolver import (
    LongTermMemoryResolver,
)
from backend.memory.context_builder import ContextEntry


class TestLongTermMemoryResolver:
    """Tests for long-term memory question detection and resolution."""

    def test_detects_long_term_memory_question(self):
        """Future-conversation questions should enter the structured mode."""
        assert (
            LongTermMemoryResolver.is_long_term_memory_question(
                "If I return tomorrow, what answer style should you still remember?",
            )
            is True
        )

    def test_non_long_term_memory_question_returns_false(self):
        """General memory lookup should not enter the long-term-only mode."""
        assert (
            LongTermMemoryResolver.is_long_term_memory_question(
                "Ты мне говорил про настройки Ollama?",
            )
            is False
        )

    def test_builds_current_style_response_from_reflection(self):
        """Detailed durable summaries should win over temporary context."""
        resolver = LongTermMemoryResolver()
        context_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] The user's preference has shifted "
                    "from brief answers to detailed, step-by-step technical and "
                    "debugging responses with full context and reasoning for "
                    "future conversations."
                ),
            ),
            ContextEntry(
                role="record",
                content=(
                    "[Retrieved memory] This validation run is temporary "
                    "session context and should stay separate from the durable "
                    "preference."
                ),
            ),
        ]

        response_text = resolver.build_response(
            context_entries,
            session_id="memory-loop-1-demo",
            question_text=(
                "If I return tomorrow, what answer style should you still remember?"
            ),
        )

        assert "Long-term preference:" in response_text
        assert "Detailed, step-by-step answers" in response_text
        assert "Temporary short-term context:" in response_text
        assert "memory-loop-1-demo" in response_text
        assert "validation run" not in response_text

    def test_builds_old_style_response_when_only_old_signal_exists(self):
        """Old concise style should remain when no newer durable shift exists."""
        resolver = LongTermMemoryResolver()
        context_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] The default long-term preference "
                    "is brief, direct, concise replies."
                ),
            ),
        ]

        response_text = resolver.build_response(
            context_entries,
            session_id="",
            question_text=(
                "If I return tomorrow, what answer style should you still remember?"
            ),
        )

        assert "Long-term preference:" in response_text
        assert "Brief, direct answers" in response_text
        assert "validation run" not in response_text

    def test_builds_russian_response_when_question_is_russian(self):
        """Russian long-term questions should get the same structure in Russian."""
        resolver = LongTermMemoryResolver()
        context_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] The user's preference has shifted "
                    "from brief answers to detailed, step-by-step technical and "
                    "debugging responses with full context and reasoning for "
                    "future conversations."
                ),
            ),
        ]

        response_text = resolver.build_response(
            context_entries,
            session_id="demo-session",
            question_text=(
                "Если я вернусь завтра, какой стиль ответа ты должен помнить?"
            ),
        )

        assert "Долгосрочное предпочтение:" in response_text
        assert "Подробные пошаговые ответы" in response_text
        assert "Временный краткосрочный контекст:" in response_text
        assert "demo-session" in response_text

    def test_builds_spanish_response_when_question_is_spanish(self):
        """Spanish long-term questions should get the same structure in Spanish."""
        resolver = LongTermMemoryResolver()
        context_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] The user's preference has shifted "
                    "from brief answers to detailed, step-by-step technical and "
                    "debugging responses with full context and reasoning for "
                    "future conversations."
                ),
            ),
        ]

        response_text = resolver.build_response(
            context_entries,
            session_id="demo-session",
            question_text="Si vuelvo mañana, qué estilo de respuesta debes recordar?",
        )

        assert "Preferencia a largo plazo:" in response_text
        assert "Respuestas detalladas, paso a paso" in response_text
        assert "Contexto temporal de corto plazo:" in response_text
