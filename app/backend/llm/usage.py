"""LLM usage collection helpers for chat responses."""

from __future__ import annotations

from dataclasses import dataclass

from backend.models.chat_stats import (
    ContextUsage,
    ExtendedUsage,
    MemoryZones,
    StageTiming,
    StageUsage,
    TimingStats,
    ZoneBudgetItem,
)


PROMPT_STAGE_LABEL = "Prompt"
GENERATION_STAGE_LABEL = "Generation"
# Drop phases too short to render as anything but "0ms". The UI shows sub-second
# times as whole milliseconds (round()), so a phase under half a millisecond —
# e.g. context assembly, which is just string building — would read as "0ms".
MIN_VISIBLE_STAGE_MS = 0.5


def _build_stage_timings(
    stage_phases: list[tuple[str, float]] | None,
    prompt_ms: float,
    generation_ms: float,
) -> list[StageTiming]:
    """Build the ordered per-phase breakdown, dropping phases that read as zero.

    ``stage_phases`` holds the wall-clock spans measured during the turn
    (memory recall, context assembly, tools) in execution order. Repeated tool
    labels are summed while keeping first-seen order. The engine-reported prompt
    and generation times are appended last. Phases are *durations*, not a
    partition of ``total_ms`` — they may overlap and are not summed to a total.
    """
    ordered_labels: list[str] = []
    totals: dict[str, float] = {}
    for label, duration_ms in stage_phases or []:
        if label not in totals:
            ordered_labels.append(label)
        totals[label] = totals.get(label, 0.0) + duration_ms

    combined = [(label, totals[label]) for label in ordered_labels]
    combined.append((PROMPT_STAGE_LABEL, prompt_ms))
    combined.append((GENERATION_STAGE_LABEL, generation_ms))

    return [
        StageTiming(label=label, duration_ms=round(duration_ms, 1))
        for label, duration_ms in combined
        if duration_ms >= MIN_VISIBLE_STAGE_MS
    ]


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
    phase: str = ""
    attempt: int = 0
    tool_capable: bool = False
    tool_used: bool = False
    context_total: int | None = None
    context_used: int | None = None


@dataclass(frozen=True)
class PhaseCallStats:
    """Aggregated usage for one phase (e.g. Stage 1, Stage 2).

    Attributes:
        phase: Phase label.
        call_count: Number of provider calls in this phase (includes retries).
        retries: call_count minus one (zero for single-attempt phases).
        prompt_tokens: Sum of prompt tokens across calls in this phase.
        completion_tokens: Sum of completion tokens across calls in this phase.
        prompt_ms: Sum of prompt timing across calls in this phase.
        generation_ms: Sum of generation timing across calls in this phase.
    """

    phase: str
    call_count: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_ms: float = 0.0
    generation_ms: float = 0.0


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


def _to_stage_usage(ps: PhaseCallStats | None, model: str) -> StageUsage | None:
    """Convert per-phase aggregated stats into a StageUsage object."""
    if ps is None:
        return None
    return StageUsage(
        prompt_tokens=ps.prompt_tokens,
        completion_tokens=ps.completion_tokens,
        total_tokens=ps.prompt_tokens + ps.completion_tokens,
        model=model,
        prompt_ms=round(ps.prompt_ms, 1),
        generation_ms=round(ps.generation_ms, 1),
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
    phase_stats: list[PhaseCallStats] | None = None,
    context_total: int | None = None,
    zone_usage: dict[str, int] | None = None,
    stage_phases: list[tuple[str, float]] | None = None,
    tool_capable: bool = False,
    tool_used: bool = False,
) -> ExtendedUsage:
    """Build the final aggregated usage object."""
    phase_map = {ps.phase: ps for ps in (phase_stats or [])}
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
            stages=_build_stage_timings(stage_phases, prompt_ms, generation_ms),
        ),
        model=model,
        iterations=iterations,
        stage1=_to_stage_usage(phase_map.get("Stage 1"), model),
        stage2=_to_stage_usage(phase_map.get("Stage 2"), model),
        tool_capable=tool_capable,
        tool_used=tool_used,
    )


def _usage_totals(
    call_usages: list[LLMCallUsage],
) -> tuple[int, int, float, float, list[PhaseCallStats]]:
    """Return aggregated token/timing totals and per-phase breakdown.

    Returns:
        Tuple of (prompt_tokens, completion_tokens, prompt_ms, generation_ms,
        phase_stats).  ``phase_stats`` is ordered by first-seen phase label.
    """
    totals = [0, 0, 0.0, 0.0]
    ordered_phases: list[str] = []
    phase_counters: dict[str, dict] = {}
    for call_usage in call_usages:
        phase = call_usage.phase or "unknown"
        totals[0] += call_usage.prompt_tokens
        totals[1] += call_usage.completion_tokens
        totals[2] += call_usage.prompt_ms
        totals[3] += call_usage.generation_ms

        if phase not in phase_counters:
            ordered_phases.append(phase)
            phase_counters[phase] = {
                "call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "prompt_ms": 0.0,
                "generation_ms": 0.0,
            }
        counters = phase_counters[phase]
        counters["call_count"] += 1
        counters["prompt_tokens"] += call_usage.prompt_tokens
        counters["completion_tokens"] += call_usage.completion_tokens
        counters["prompt_ms"] += call_usage.prompt_ms
        counters["generation_ms"] += call_usage.generation_ms

    phase_stats = [
        PhaseCallStats(
            phase=phase,
            call_count=phase_counters[phase]["call_count"],
            retries=max(0, phase_counters[phase]["call_count"] - 1),
            prompt_tokens=phase_counters[phase]["prompt_tokens"],
            completion_tokens=phase_counters[phase]["completion_tokens"],
            prompt_ms=phase_counters[phase]["prompt_ms"],
            generation_ms=phase_counters[phase]["generation_ms"],
        )
        for phase in ordered_phases
    ]
    return (*tuple(totals[:4]), phase_stats)


def aggregate_call_usages(
    call_usages: list[LLMCallUsage],
    *,
    fallback_model: str,
    total_wall_time_ms: float,
    zone_usage: dict[str, int] | None = None,
    stage_phases: list[tuple[str, float]] | None = None,
) -> ExtendedUsage | None:
    """Aggregate provider call usage data into response statistics.

    Groups calls by phase label (Stage 1, Stage 2, etc.), counts retries per
    phase, and reports the number of distinct phases as iterations.
    """
    if not call_usages:
        return None

    prompt_tokens, completion_tokens, prompt_ms, generation_ms, phase_stats = (
        _usage_totals(call_usages)
    )

    if prompt_ms == ZERO_DURATION_MS and generation_ms == ZERO_DURATION_MS:
        # The provider reported no per-phase timing (e.g. an OpenAI-compatible
        # endpoint returns token counts only). Treat generation as the remainder
        # of the turn after the phases we measured ourselves (recall, context,
        # tools), so the breakdown partitions total time instead of counting
        # those phases twice — once on their own line and again inside
        # "Generation".
        measured_phase_ms = sum(ms for _, ms in (stage_phases or []))
        generation_ms = max(ZERO_DURATION_MS, total_wall_time_ms - measured_phase_ms)
    latest_usage = call_usages[-1]

    # iterations = provider round-trips: the chat UI presents this as
    # "N iterations" of the agent loop, so it must count calls, not the
    # 1-2 distinct phase labels.
    iterations = len(call_usages)

    return _build_extended_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_ms=prompt_ms,
        generation_ms=generation_ms,
        total_wall_time_ms=total_wall_time_ms,
        context_usage=_context_usage_from_latest(latest_usage),
        model=latest_usage.model or fallback_model,
        iterations=iterations,
        phase_stats=phase_stats,
        context_total=latest_usage.context_total,
        zone_usage=zone_usage,
        stage_phases=stage_phases,
        tool_capable=any(usage.tool_capable for usage in call_usages),
        tool_used=any(usage.tool_used for usage in call_usages),
    )
