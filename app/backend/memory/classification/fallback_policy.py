"""Policy helpers for deciding when semantic fallback is worth trying."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.memory.classification.models import MemoryQuestionIntent


if TYPE_CHECKING:
    from backend.memory.classification.question_router import MemoryQuestionRoute


UNKNOWN_LANGUAGE_QUESTION_MIN_LENGTH = 12
UNKNOWN_LANGUAGE_QUESTION_MAX_LENGTH = 240
UNKNOWN_LANGUAGE_QUESTION_MIN_WORDS = 4
CONFLICTING_SIGNALS_REASON = "conflicting_signals"
UNSUPPORTED_LANGUAGE_QUESTION_REASON = "unsupported_language_question_shape"
SUPPORTED_LITHUANIAN_QUESTION_MARKERS = (
    "ar gali",
    "parašyti",
    "prisimeni",
    "primink",
    "apie ką",
    "ką mes aptarėme",
)
UNKNOWN_LANGUAGE_NON_MEMORY_TECHNICAL_MARKERS = (
    "python",
    "javascript",
    "typescript",
    "bash",
    "sql",
    "json",
    "api",
    "function",
    "función",
    "funktion",
    "funkce",
    "example",
    "ejemplo",
    "beispiel",
    "příklad",
    "kodas",
)


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    """Return whether one normalized text contains any marker."""
    return any(marker in text for marker in markers)


def _is_cyrillic_text(text: str) -> bool:
    """Return whether one string contains any Cyrillic characters."""
    return any("\u0400" <= character <= "\u04ff" for character in text)


def _looks_like_supported_non_memory_question(normalized_text: str) -> bool:
    """Return whether the text already looks like a supported non-memory query."""
    if _is_cyrillic_text(normalized_text):
        return True
    if _contains_marker(
        normalized_text,
        UNKNOWN_LANGUAGE_NON_MEMORY_TECHNICAL_MARKERS,
    ):
        return True
    return _contains_marker(
        normalized_text,
        SUPPORTED_LITHUANIAN_QUESTION_MARKERS,
    )


def looks_like_unknown_language_question(text: str) -> bool:
    """Return whether a message looks like a compact user question.

    This heuristic is intentionally broad. It is used only when the direct
    signal layer found no memory intent at all, so the system can still try a
    semantic fallback for unsupported languages instead of failing closed on
    every non-English question.
    """
    normalized_text = text.strip()
    if not normalized_text.endswith("?"):
        return False
    if not (
        UNKNOWN_LANGUAGE_QUESTION_MIN_LENGTH
        <= len(normalized_text)
        <= UNKNOWN_LANGUAGE_QUESTION_MAX_LENGTH
    ):
        return False
    if len(normalized_text.split()) < UNKNOWN_LANGUAGE_QUESTION_MIN_WORDS:
        return False
    if normalized_text.isascii():
        return False
    casefolded_text = normalized_text.casefold()
    return not _looks_like_supported_non_memory_question(casefolded_text)


def should_use_semantic_fallback(
    route: MemoryQuestionRoute,
    text: str,
) -> bool:
    """Return whether the semantic fallback classifier should be tried."""
    return semantic_fallback_reason(route, text) is not None


def semantic_fallback_reason(
    route: MemoryQuestionRoute,
    text: str,
) -> str | None:
    """Return the reason why semantic fallback should be tried, if any."""
    if route.fallback_recommended:
        return CONFLICTING_SIGNALS_REASON
    if route.intent is not MemoryQuestionIntent.NON_MEMORY_QUESTION:
        return None
    if looks_like_unknown_language_question(text):
        return UNSUPPORTED_LANGUAGE_QUESTION_REASON
    return None
