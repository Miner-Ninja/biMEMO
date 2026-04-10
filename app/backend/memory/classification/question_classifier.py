"""Question classification helpers for memory-related user queries."""

from __future__ import annotations

from backend.memory.classification.models import (
    ClassificationConfidence,
    MemoryQuestionClassification,
    MemoryQuestionIntent,
    build_memory_question_classification,
)
from backend.memory.classification.signals import (
    MemoryQuestionSignals,
)


ASSISTANT_RECALL_SIGNAL_GROUP = "assistant_recall_signals"
USER_RECALL_SIGNAL_GROUP = "user_recall_signals"
GENERAL_MEMORY_RECALL_SIGNAL_GROUP = "general_memory_recall_signals"
LONG_TERM_MEMORY_SIGNAL_GROUP = "long_term_memory_signals"
DURABLE_PREFERENCE_SIGNAL_GROUP = "durable_preference_signals"
TEMPORARY_CONTEXT_SIGNAL_GROUP = "temporary_context_signals"


def _has_conflicting_memory_preference_signals(
    matched_signal_set: set[str],
) -> bool:
    """Return whether the matched signals mix durable and temporary context."""
    return bool(
        {
            DURABLE_PREFERENCE_SIGNAL_GROUP,
            TEMPORARY_CONTEXT_SIGNAL_GROUP,
        }
        & matched_signal_set
    )


def _classification(
    *,
    intent: MemoryQuestionIntent,
    matched_signal_groups: tuple[str, ...],
    role_filter: str | None = None,
    memory_query: bool = True,
    long_term_memory_question: bool = False,
    confidence: ClassificationConfidence,
    fallback_recommended: bool = False,
) -> MemoryQuestionClassification:
    """Build one question-classification result with the local defaults."""
    return build_memory_question_classification(
        intent=intent,
        role_filter=role_filter,
        memory_query=memory_query,
        long_term_memory_question=long_term_memory_question,
        confidence=confidence,
        matched_signal_groups=matched_signal_groups,
        fallback_recommended=fallback_recommended,
    )


def classify_memory_question(text: str) -> MemoryQuestionClassification:
    """Classify one user question for memory-specific routing.

    Args:
        text: Raw user question.

    Returns:
        Structured classification result with intent and confidence.
    """
    matched_signal_groups = MemoryQuestionSignals.match_groups(text)
    matched_signal_set = set(matched_signal_groups)

    if LONG_TERM_MEMORY_SIGNAL_GROUP in matched_signal_set:
        return _classification(
            intent=MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION,
            long_term_memory_question=True,
            confidence=ClassificationConfidence.HIGH,
            matched_signal_groups=matched_signal_groups,
        )

    if ASSISTANT_RECALL_SIGNAL_GROUP in matched_signal_set:
        return _classification(
            intent=MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
            role_filter="assistant",
            confidence=ClassificationConfidence.HIGH,
            matched_signal_groups=matched_signal_groups,
        )

    if USER_RECALL_SIGNAL_GROUP in matched_signal_set:
        return _classification(
            intent=MemoryQuestionIntent.USER_RECALL_QUESTION,
            role_filter="user",
            confidence=ClassificationConfidence.HIGH,
            matched_signal_groups=matched_signal_groups,
        )

    if GENERAL_MEMORY_RECALL_SIGNAL_GROUP in matched_signal_set:
        return _classification(
            intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
            confidence=ClassificationConfidence.MEDIUM,
            matched_signal_groups=matched_signal_groups,
            fallback_recommended=_has_conflicting_memory_preference_signals(
                matched_signal_set
            ),
        )

    if {
        DURABLE_PREFERENCE_SIGNAL_GROUP,
        TEMPORARY_CONTEXT_SIGNAL_GROUP,
    } <= matched_signal_set:
        return _classification(
            intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
            confidence=ClassificationConfidence.LOW,
            matched_signal_groups=matched_signal_groups,
            fallback_recommended=True,
        )

    return _classification(
        intent=MemoryQuestionIntent.NON_MEMORY_QUESTION,
        memory_query=False,
        confidence=ClassificationConfidence.NONE,
        matched_signal_groups=matched_signal_groups,
    )


def detect_message_role_filter(text: str) -> str | None:
    """Detect whether the user wants assistant or user messages only."""
    classification = classify_memory_question(text)
    return classification.role_filter


def is_memory_query(text: str) -> bool:
    """Detect whether a query is primarily asking for remembered context."""
    return classify_memory_question(text).memory_query


def is_persistence_question(text: str) -> bool:
    """Detect long-term memory questions about what should remain durable."""
    return classify_memory_question(text).long_term_memory_question
