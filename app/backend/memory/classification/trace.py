"""Trace helpers for comparing direct and fallback memory-question routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.memory.classification.question_router import MemoryQuestionRoute


def _route_scalar_fields(route: MemoryQuestionRoute) -> dict[str, object]:
    """Return the route fields shared by mismatch checks and trace output."""
    return {
        "intent": route.intent,
        "role_filter": route.role_filter,
        "memory_query": route.memory_query,
        "long_term_memory_question": route.long_term_memory_question,
        "confidence": route.confidence,
        "fallback_recommended": route.fallback_recommended,
        "source": route.source,
        "fallback_rationale": route.fallback_rationale,
    }


def _route_summary(route: MemoryQuestionRoute | None) -> dict[str, object] | None:
    """Return one serializable snapshot of a route object."""
    if route is None:
        return None
    scalar_fields = _route_scalar_fields(route)
    return {
        "intent": scalar_fields["intent"].value,
        "role_filter": scalar_fields["role_filter"],
        "memory_query": scalar_fields["memory_query"],
        "long_term_memory_question": scalar_fields["long_term_memory_question"],
        "confidence": scalar_fields["confidence"].value,
        "fallback_recommended": scalar_fields["fallback_recommended"],
        "source": scalar_fields["source"],
        "matched_signal_groups": list(route.matched_signal_groups),
        "fallback_rationale": scalar_fields["fallback_rationale"],
    }


def _route_values(route: MemoryQuestionRoute) -> tuple[object, ...]:
    """Return the route fields that matter for mismatch comparison."""
    scalar_fields = _route_scalar_fields(route)
    return (
        scalar_fields["intent"],
        scalar_fields["role_filter"],
        scalar_fields["long_term_memory_question"],
        scalar_fields["source"],
    )


def _route_mismatch_label(
    initial_route: MemoryQuestionRoute,
    final_route: MemoryQuestionRoute,
) -> str:
    """Return a stable label describing the first route difference."""
    if initial_route.intent != final_route.intent:
        return "intent_changed"
    if initial_route.role_filter != final_route.role_filter:
        return "role_filter_changed"
    if initial_route.long_term_memory_question != final_route.long_term_memory_question:
        return "long_term_flag_changed"
    if initial_route.source != final_route.source:
        return "source_changed"
    return "no_mismatch"


@dataclass(frozen=True)
class MemoryQuestionRouteTrace:
    """One trace snapshot for signal, fallback, and final memory routing."""

    initial_route: MemoryQuestionRoute
    final_route: MemoryQuestionRoute
    fallback_attempted: bool
    fallback_reason: str | None
    fallback_route: MemoryQuestionRoute | None

    @property
    def fallback_overrode_signal_route(self) -> bool:
        """Return whether fallback changed the final route."""
        return (
            self.fallback_route is not None
            and self.final_route.source == "semantic_fallback"
        )

    @property
    def route_mismatch(self) -> bool:
        """Return whether the final route differs from the direct signal route."""
        return _route_values(self.initial_route) != _route_values(self.final_route)

    @property
    def mismatch_label(self) -> str:
        """Return a stable label for the route difference category."""
        return _route_mismatch_label(self.initial_route, self.final_route)

    def to_dict(self) -> dict[str, object]:
        """Return a serializable shadow summary for reporting and logs."""
        return {
            "fallback_attempted": self.fallback_attempted,
            "fallback_reason": self.fallback_reason,
            "fallback_overrode_signal_route": self.fallback_overrode_signal_route,
            "route_mismatch": self.route_mismatch,
            "mismatch_label": self.mismatch_label,
            "initial_route": _route_summary(self.initial_route),
            "fallback_route": _route_summary(self.fallback_route),
            "final_route": _route_summary(self.final_route),
        }
