"""Phase summary helpers for the memory loop evaluation harness."""

from __future__ import annotations

from scripts.memory_loop_constants import PHASE_COUNT, PHASE_NAMES
from scripts.memory_loop_matchers import (
    response_only_mentions_old_style,
    response_prefers_current_style,
)
from scripts.memory_loop_models import ChatDialogStepResult, PhaseSummary


def _slice_dialog_step_results_by_phase(
    dialog_step_results: list[ChatDialogStepResult],
) -> dict[str, list[ChatDialogStepResult]]:
    """Split dialog-step results into early, middle, and late phases."""
    if not dialog_step_results:
        return {phase_name: [] for phase_name in PHASE_NAMES}

    phase_slices: dict[str, list[ChatDialogStepResult]] = {}
    total_dialog_steps = len(dialog_step_results)
    for phase_index, phase_name in enumerate(PHASE_NAMES):
        start_index = (phase_index * total_dialog_steps) // PHASE_COUNT
        end_index = ((phase_index + 1) * total_dialog_steps) // PHASE_COUNT
        phase_slices[phase_name] = dialog_step_results[start_index:end_index]
    return phase_slices


def _summarize_phase(dialog_step_results: list[ChatDialogStepResult]) -> PhaseSummary:
    """Build one compact summary for a slice of scenario dialog steps."""
    if not dialog_step_results:
        return PhaseSummary(
            dialog_step_count=0,
            avg_latency_seconds=0.0,
            avg_retrieved_memories=0.0,
            avg_retrieved_messages=0.0,
            current_style_response_rate=0.0,
            old_style_only_response_rate=0.0,
            error_count=0,
        )

    total_dialog_steps = len(dialog_step_results)
    error_count = sum(
        1 for dialog_step_result in dialog_step_results if dialog_step_result.error
    )
    current_style_hits = sum(
        1
        for dialog_step_result in dialog_step_results
        if response_prefers_current_style(dialog_step_result.response_text)
    )
    old_style_only_hits = sum(
        1
        for dialog_step_result in dialog_step_results
        if response_only_mentions_old_style(dialog_step_result.response_text)
    )
    return PhaseSummary(
        dialog_step_count=total_dialog_steps,
        avg_latency_seconds=(
            sum(
                dialog_step_result.elapsed_seconds
                for dialog_step_result in dialog_step_results
            )
            / total_dialog_steps
        ),
        avg_retrieved_memories=(
            sum(
                len(dialog_step_result.retrieved_memories)
                for dialog_step_result in dialog_step_results
            )
            / total_dialog_steps
        ),
        avg_retrieved_messages=(
            sum(
                len(dialog_step_result.retrieved_messages)
                for dialog_step_result in dialog_step_results
            )
            / total_dialog_steps
        ),
        current_style_response_rate=current_style_hits / total_dialog_steps,
        old_style_only_response_rate=old_style_only_hits / total_dialog_steps,
        error_count=error_count,
    )


def build_phase_breakdown(
    dialog_step_results: list[ChatDialogStepResult],
) -> dict[str, PhaseSummary]:
    """Summarize early, middle, and late behavior of one scenario run."""
    phase_slices = _slice_dialog_step_results_by_phase(dialog_step_results)
    return {
        phase_name: _summarize_phase(phase_dialog_steps)
        for phase_name, phase_dialog_steps in phase_slices.items()
    }
