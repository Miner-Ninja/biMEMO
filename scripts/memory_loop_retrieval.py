"""Retrieved-memory reuse diagnostics for the memory loop harness."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from scripts.memory_loop_constants import PHASE_COUNT, PHASE_NAMES


if TYPE_CHECKING:
    from scripts.memory_loop_models import ChatDialogStepResult


def _normalize_retrieved_memory_summary(entry: dict[str, Any]) -> str:
    """Normalize one retrieved-memory summary before counting reuse."""
    return " ".join(str(entry.get("summary", "")).split()).strip()


def _slice_dialog_step_results_by_phase(
    dialog_step_results: list[ChatDialogStepResult],
) -> dict[str, list[ChatDialogStepResult]]:
    """Split dialog steps into early, middle, and late phases."""
    if not dialog_step_results:
        return {phase_name: [] for phase_name in PHASE_NAMES}

    phase_slices: dict[str, list[ChatDialogStepResult]] = {}
    total_dialog_steps = len(dialog_step_results)
    for phase_index, phase_name in enumerate(PHASE_NAMES):
        start_index = (phase_index * total_dialog_steps) // PHASE_COUNT
        end_index = ((phase_index + 1) * total_dialog_steps) // PHASE_COUNT
        phase_slices[phase_name] = dialog_step_results[start_index:end_index]
    return phase_slices


def _summarize_summary_reuse(
    dialog_step_results: list[ChatDialogStepResult],
) -> dict[str, Any]:
    """Summarize whether a few memory summaries dominate retrieval."""
    summaries = [
        normalized_summary
        for dialog_step_result in dialog_step_results
        for entry in dialog_step_result.retrieved_memories
        if (normalized_summary := _normalize_retrieved_memory_summary(entry))
    ]
    if not summaries:
        return {
            "retrieved_memory_hits": 0,
            "unique_retrieved_memory_summaries": 0,
            "dominant_summary": "",
            "dominant_summary_hits": 0,
            "dominant_summary_share": 0.0,
        }

    summary_counter = Counter(summaries)
    dominant_summary, dominant_summary_hits = summary_counter.most_common(1)[0]
    return {
        "retrieved_memory_hits": len(summaries),
        "unique_retrieved_memory_summaries": len(summary_counter),
        "dominant_summary": dominant_summary,
        "dominant_summary_hits": dominant_summary_hits,
        "dominant_summary_share": dominant_summary_hits / len(summaries),
    }


def build_retrieval_report(
    dialog_step_results: list[ChatDialogStepResult],
) -> dict[str, Any]:
    """Build reuse diagnostics for all retrieval hits and the late phase only."""
    full_report = _summarize_summary_reuse(dialog_step_results)
    late_phase_report = _summarize_summary_reuse(
        _slice_dialog_step_results_by_phase(dialog_step_results)["late"]
    )
    return {
        "retrieved_memory_hits": full_report["retrieved_memory_hits"],
        "unique_retrieved_memory_summaries": (
            full_report["unique_retrieved_memory_summaries"]
        ),
        "dominant_summary": full_report["dominant_summary"],
        "dominant_summary_hits": full_report["dominant_summary_hits"],
        "dominant_summary_share": full_report["dominant_summary_share"],
        "late_phase_retrieved_memory_hits": late_phase_report["retrieved_memory_hits"],
        "late_phase_unique_retrieved_memory_summaries": (
            late_phase_report["unique_retrieved_memory_summaries"]
        ),
        "late_phase_dominant_summary": late_phase_report["dominant_summary"],
        "late_phase_dominant_summary_hits": late_phase_report["dominant_summary_hits"],
        "late_phase_dominant_summary_share": late_phase_report[
            "dominant_summary_share"
        ],
    }
