"""LLM usage collection helpers for chat responses."""

from __future__ import annotations

from dataclasses import dataclass

from backend.models.chat_stats import (
    ContextUsage,
    ExtendedUsage,
    MemoryZones,
    TimingStats,
    ZoneBudgetItem,
)


ZERO_DURATION_MS = 0.0


@dataclass(frozen=True)
class LLMCallUsage:
    """Usage data collected from a single provider call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_ms: float = 0.0
    generation_ms: float = 0.0
    total_ms: float = 0.0
    model: str = ""
    context_total: int | None = None
    context_used: int | None = None


def _context_usage_from_latest(latest_usage: LLMCallUsage) -> ContextUsage | None:
    """Build context-usage stats from the latest provider call."""
    if (
        latest_usage.context_total is None
        or latest_usage.context_total <= 0
        or latest_usage.context_used is None
    ):
        return None

    context_free = max(0, latest_usage.context_total - latest_usage.context_used)
    used_percent = (latest_usage.context_used / latest_usage.context_total) * 100
    return ContextUsage(
        total=latest_usage.context_total,
        used=latest_usage.context_used,
        free=context_free,
        used_percent=round(used_percent, 1),
    )


def _build_memory_zones(
    context_total: int | None,
    zone_usage: dict[str, int] | None = None,
) -> MemoryZones | None:
    """Build memory zone budget with usage from the LLM context window."""
    if context_total is None or context_total <= 0:
        return None
    from backend.memory.context_builder.models import ContextZoneBudget

    budget = ContextZoneBudget.from_context_window(num_ctx=context_total)
    usage = zone_usage or {}
    return MemoryZones(
        working_memory=ZoneBudgetItem(
            budget=budget.working_memory,
            used=usage.get("working_memory", 0),
            label="Working",
        ),
        recall_results=ZoneBudgetItem(
            budget=budget.recall_results,
            used=usage.get("recall_results", 0),
            label="Recall",
        ),
        conversation_summary=ZoneBudgetItem(
            budget=budget.conversation_summary,
            used=usage.get("conversation_summary", 0),
            label="Summary",
        ),
        total_available=budget.total_available,
    )


def _build_extended_usage(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_ms: float,
    generation_ms: float,
    total_wall_time_ms: float,
    context_usage: ContextUsage | None,
    model: str,
    iterations: int,
    context_total: int | None = None,
    zone_usage: dict[str, int] | None = None,
) -> ExtendedUsage:
    """Build the final aggregated usage object."""
    return ExtendedUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        context=context_usage,
        memory_zones=_build_memory_zones(context_total, zone_usage),
        timing=TimingStats(
            prompt_ms=round(prompt_ms, 1),
            generation_ms=round(generation_ms, 1),
            total_ms=round(total_wall_time_ms, 1),
        ),
        model=model,
        iterations=iterations,
    )


def _usage_totals(call_usages: list[LLMCallUsage]) -> tuple[int, int, float, float]:
    """Return aggregated token and timing totals across provider calls."""
    return (
        sum(call_usage.prompt_tokens for call_usage in call_usages),
        sum(call_usage.completion_tokens for call_usage in call_usages),
        sum(call_usage.prompt_ms for call_usage in call_usages),
        sum(call_usage.generation_ms for call_usage in call_usages),
    )


def aggregate_call_usages(
    call_usages: list[LLMCallUsage],
    *,
    fallback_model: str,
    total_wall_time_ms: float,
    zone_usage: dict[str, int] | None = None,
) -> ExtendedUsage | None:
    """Aggregate provider call usage data into response statistics."""
    if not call_usages:
        return None

    prompt_tokens, completion_tokens, prompt_ms, generation_ms = _usage_totals(
        call_usages
    )
    if prompt_ms == ZERO_DURATION_MS and generation_ms == ZERO_DURATION_MS:
        generation_ms = total_wall_time_ms
    latest_usage = call_usages[-1]

    return _build_extended_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_ms=prompt_ms,
        generation_ms=generation_ms,
        total_wall_time_ms=total_wall_time_ms,
        context_usage=_context_usage_from_latest(latest_usage),
        model=latest_usage.model or fallback_model,
        iterations=len(call_usages),
        context_total=latest_usage.context_total,
        zone_usage=zone_usage,
    )
