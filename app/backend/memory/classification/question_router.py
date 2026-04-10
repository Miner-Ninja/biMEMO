"""Routing helpers for memory-related question handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.memory.classification.question_classifier import (
    classify_memory_question,
)


if TYPE_CHECKING:
    from backend.memory.classification.models import (
        ClassificationConfidence,
        MemoryQuestionClassification,
        MemoryQuestionIntent,
    )


def _route_fields(
    classification: MemoryQuestionClassification,
) -> dict[str, object]:
    """Return the route fields shared by all route-building entry points."""
    return {
        "role_filter": classification.role_filter,
        "memory_query": classification.memory_query,
        "long_term_memory_question": classification.long_term_memory_question,
        "intent": classification.intent,
        "confidence": classification.confidence,
        "fallback_recommended": classification.fallback_recommended,
        "matched_signal_groups": classification.matched_signal_groups,
    }


@dataclass(frozen=True)
class MemoryQuestionRoute:
    """Classification result for one memory-related user question."""

    role_filter: str | None
    memory_query: bool
    long_term_memory_question: bool
    intent: MemoryQuestionIntent
    confidence: ClassificationConfidence
    fallback_recommended: bool
    source: str
    matched_signal_groups: tuple[str, ...]
    fallback_rationale: str | None

    @classmethod
    def from_classification(
        cls,
        classification: MemoryQuestionClassification,
        *,
        source: str = "signal",
        fallback_rationale: str | None = None,
    ) -> MemoryQuestionRoute:
        """Build one route directly from a classification result."""
        fields = _route_fields(classification)
        return cls(
            role_filter=fields["role_filter"],
            memory_query=fields["memory_query"],
            long_term_memory_question=fields["long_term_memory_question"],
            intent=fields["intent"],
            confidence=fields["confidence"],
            fallback_recommended=fields["fallback_recommended"],
            source=source,
            matched_signal_groups=fields["matched_signal_groups"],
            fallback_rationale=fallback_rationale,
        )


def route_memory_question(query_text: str) -> MemoryQuestionRoute:
    """Classify a question and return the memory handling route."""
    classification = classify_memory_question(query_text)
    return MemoryQuestionRoute.from_classification(classification)
