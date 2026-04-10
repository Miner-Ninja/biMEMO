"""Typed models for memory-question classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MemoryQuestionIntent(StrEnum):
    """Canonical intent categories for memory-question routing."""

    NON_MEMORY_QUESTION = "non_memory_question"
    MEMORY_RECALL_QUESTION = "memory_recall_question"
    ASSISTANT_RECALL_QUESTION = "assistant_recall_question"
    USER_RECALL_QUESTION = "user_recall_question"
    LONG_TERM_MEMORY_QUESTION = "long_term_memory_question"


class ClassificationConfidence(StrEnum):
    """Confidence levels for memory-question classification."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class MemoryQuestionClassification:
    """Structured result for one memory-question classification decision.

    Attributes:
        intent: Canonical detected intent.
        role_filter: Optional message role filter for retrieval.
        memory_query: Whether the question should enter memory-query mode.
        long_term_memory_question: Whether the long-term memory mode applies.
        confidence: Confidence of the classification decision.
        matched_signal_groups: Canonical signal groups that matched.
        fallback_recommended: Whether semantic fallback should be considered.
    """

    intent: MemoryQuestionIntent
    role_filter: str | None
    memory_query: bool
    long_term_memory_question: bool
    confidence: ClassificationConfidence
    matched_signal_groups: tuple[str, ...] = field(default_factory=tuple)
    fallback_recommended: bool = False


def build_memory_question_classification(
    *,
    intent: MemoryQuestionIntent,
    role_filter: str | None,
    memory_query: bool,
    long_term_memory_question: bool,
    confidence: ClassificationConfidence,
    matched_signal_groups: tuple[str, ...] = (),
    fallback_recommended: bool = False,
) -> MemoryQuestionClassification:
    """Build one memory-question classification with shared defaults."""
    return MemoryQuestionClassification(
        intent=intent,
        role_filter=role_filter,
        memory_query=memory_query,
        long_term_memory_question=long_term_memory_question,
        confidence=confidence,
        matched_signal_groups=matched_signal_groups,
        fallback_recommended=fallback_recommended,
    )


def is_memory_question_intent(intent: MemoryQuestionIntent) -> bool:
    """Return whether one intent represents a memory-query path."""
    return intent is not MemoryQuestionIntent.NON_MEMORY_QUESTION
