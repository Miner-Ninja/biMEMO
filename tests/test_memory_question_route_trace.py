"""Tests for memory-question route trace comparison."""

from backend.memory.classification.models import (
    ClassificationConfidence,
    MemoryQuestionIntent,
)
from backend.memory.classification.question_router import MemoryQuestionRoute
from backend.memory.classification.trace import MemoryQuestionRouteTrace


def _route(
    *,
    intent: MemoryQuestionIntent,
    role_filter: str | None,
    long_term_memory_question: bool,
    confidence: ClassificationConfidence,
    source: str,
) -> MemoryQuestionRoute:
    """Create a compact route fixture for trace tests."""
    return MemoryQuestionRoute(
        role_filter=role_filter,
        memory_query=intent is not MemoryQuestionIntent.NON_MEMORY_QUESTION,
        long_term_memory_question=long_term_memory_question,
        intent=intent,
        confidence=confidence,
        fallback_recommended=False,
        source=source,
        matched_signal_groups=(),
        fallback_rationale=None,
    )


def test_route_trace_detects_no_mismatch_when_final_route_matches_signal():
    """Same signal and final route should not count as mismatch."""
    signal_route = _route(
        intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
        role_filter=None,
        long_term_memory_question=False,
        confidence=ClassificationConfidence.MEDIUM,
        source="signal",
    )

    trace = MemoryQuestionRouteTrace(
        initial_route=signal_route,
        final_route=signal_route,
        fallback_attempted=False,
        fallback_reason=None,
        fallback_route=None,
    )

    assert trace.route_mismatch is False
    assert trace.fallback_overrode_signal_route is False
    assert trace.mismatch_label == "no_mismatch"
    assert trace.to_dict() == {
        "fallback_attempted": False,
        "fallback_reason": None,
        "fallback_overrode_signal_route": False,
        "route_mismatch": False,
        "mismatch_label": "no_mismatch",
        "initial_route": {
            "intent": "memory_recall_question",
            "role_filter": None,
            "memory_query": True,
            "long_term_memory_question": False,
            "confidence": "medium",
            "fallback_recommended": False,
            "source": "signal",
            "matched_signal_groups": [],
            "fallback_rationale": None,
        },
        "fallback_route": None,
        "final_route": {
            "intent": "memory_recall_question",
            "role_filter": None,
            "memory_query": True,
            "long_term_memory_question": False,
            "confidence": "medium",
            "fallback_recommended": False,
            "source": "signal",
            "matched_signal_groups": [],
            "fallback_rationale": None,
        },
    }


def test_route_trace_detects_when_fallback_overrides_signal():
    """Final route from fallback should count as a mismatch."""
    signal_route = _route(
        intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
        role_filter=None,
        long_term_memory_question=False,
        confidence=ClassificationConfidence.LOW,
        source="signal",
    )
    fallback_route = _route(
        intent=MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION,
        role_filter=None,
        long_term_memory_question=True,
        confidence=ClassificationConfidence.MEDIUM,
        source="semantic_fallback",
    )

    trace = MemoryQuestionRouteTrace(
        initial_route=signal_route,
        final_route=fallback_route,
        fallback_attempted=True,
        fallback_reason="conflicting_signals",
        fallback_route=fallback_route,
    )

    assert trace.route_mismatch is True
    assert trace.fallback_overrode_signal_route is True
    assert trace.mismatch_label == "intent_changed"


def test_route_trace_distinguishes_attempted_fallback_without_override():
    """Fallback can be attempted without changing the final route."""
    signal_route = _route(
        intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
        role_filter=None,
        long_term_memory_question=False,
        confidence=ClassificationConfidence.LOW,
        source="signal",
    )
    fallback_route = _route(
        intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
        role_filter=None,
        long_term_memory_question=False,
        confidence=ClassificationConfidence.LOW,
        source="semantic_fallback",
    )

    trace = MemoryQuestionRouteTrace(
        initial_route=signal_route,
        final_route=signal_route,
        fallback_attempted=True,
        fallback_reason="unsupported_language_question_shape",
        fallback_route=fallback_route,
    )

    assert trace.route_mismatch is False
    assert trace.fallback_overrode_signal_route is False
    assert trace.mismatch_label == "no_mismatch"


def test_route_trace_reports_role_filter_mismatch_separately():
    """Role-filter-only changes should have their own stable mismatch label."""
    signal_route = _route(
        intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
        role_filter=None,
        long_term_memory_question=False,
        confidence=ClassificationConfidence.MEDIUM,
        source="signal",
    )
    final_route = _route(
        intent=MemoryQuestionIntent.MEMORY_RECALL_QUESTION,
        role_filter="assistant",
        long_term_memory_question=False,
        confidence=ClassificationConfidence.MEDIUM,
        source="semantic_fallback",
    )

    trace = MemoryQuestionRouteTrace(
        initial_route=signal_route,
        final_route=final_route,
        fallback_attempted=True,
        fallback_reason="unsupported_language_question_shape",
        fallback_route=final_route,
    )

    assert trace.route_mismatch is True
    assert trace.mismatch_label == "role_filter_changed"


def test_route_trace_to_dict_includes_fallback_snapshot():
    """Shadow summary should include all three routing stages when present."""
    signal_route = _route(
        intent=MemoryQuestionIntent.NON_MEMORY_QUESTION,
        role_filter=None,
        long_term_memory_question=False,
        confidence=ClassificationConfidence.NONE,
        source="signal",
    )
    fallback_route = _route(
        intent=MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
        role_filter="assistant",
        long_term_memory_question=False,
        confidence=ClassificationConfidence.MEDIUM,
        source="semantic_fallback",
    )

    trace = MemoryQuestionRouteTrace(
        initial_route=signal_route,
        final_route=fallback_route,
        fallback_attempted=True,
        fallback_reason="unsupported_language_question_shape",
        fallback_route=fallback_route,
    )

    summary = trace.to_dict()

    assert summary["fallback_attempted"] is True
    assert summary["fallback_reason"] == "unsupported_language_question_shape"
    assert summary["fallback_overrode_signal_route"] is True
    assert summary["route_mismatch"] is True
    assert summary["mismatch_label"] == "intent_changed"
    assert summary["initial_route"] == {
        "intent": "non_memory_question",
        "role_filter": None,
        "memory_query": False,
        "long_term_memory_question": False,
        "confidence": "none",
        "fallback_recommended": False,
        "source": "signal",
        "matched_signal_groups": [],
        "fallback_rationale": None,
    }
    assert summary["fallback_route"] == {
        "intent": "assistant_recall_question",
        "role_filter": "assistant",
        "memory_query": True,
        "long_term_memory_question": False,
        "confidence": "medium",
        "fallback_recommended": False,
        "source": "semantic_fallback",
        "matched_signal_groups": [],
        "fallback_rationale": None,
    }
    assert summary["final_route"] == summary["fallback_route"]
