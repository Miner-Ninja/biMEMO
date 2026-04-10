"""Evaluation helpers for the memory loop harness."""

from __future__ import annotations

from scripts.memory_loop_constants import (
    DISTILLATION_TRIGGER_THRESHOLD,
    EXPECTED_MESSAGE_MULTIPLIER,
    MAX_POST_CHAT_FIRST_OBSERVATION_SECONDS,
    MIN_GLOBAL_QUALITY_SCORE,
    MIN_OBSERVATION_PROVENANCE_COVERAGE,
    MIN_STRUCTURED_OBSERVATION_COVERAGE,
)
from scripts.memory_loop_matchers import (
    extract_relation_labels,
    normalize_string_list,
    response_confirms_current_long_term_style,
    response_has_old_style_as_active_long_term,
)
from scripts.memory_loop_models import (
    ChatDialogStepResult,
    EvaluationFinding,
    ScenarioReport,
    ScenarioState,
)
from scripts.memory_loop_phase_summary import build_phase_breakdown
from scripts.memory_loop_retrieval import build_retrieval_report


def build_findings(
    state: ScenarioState,
    dialog_step_results: list[ChatDialogStepResult],
) -> list[EvaluationFinding]:
    """Build scenario findings from memory state and final chat outputs."""
    findings: list[EvaluationFinding] = []
    scenario_message_count = len(state.messages)
    scenario_record_count = len(state.records)
    scenario_summary_count = len(state.summaries)
    record_provenance_count = sum(
        1
        for record in state.records
        if normalize_string_list(record.get("source_message_ids", []))
    )
    structured_record_count = sum(
        1
        for record in state.records
        if normalize_string_list(record.get("key_facts", []))
        or normalize_string_list(record.get("action_items", []))
    )
    valid_relation_summary_count = sum(
        1
        for summary in state.summaries
        if extract_relation_labels(summary).issubset(
            {"supports", "conflicts", "outdates"}
        )
    )
    relation_variety = set()
    for summary in state.summaries:
        relation_variety.update(extract_relation_labels(summary))
    last_response = dialog_step_results[-1].response_text if dialog_step_results else ""
    error_count = sum(
        1 for dialog_step_result in dialog_step_results if dialog_step_result.error
    )
    quality_score = int(state.quality_report.get("quality_score", 0))
    summary_threshold_reached = (
        state.scenario_unreflected_record_count >= DISTILLATION_TRIGGER_THRESHOLD
    )

    record_provenance_coverage = (
        record_provenance_count / scenario_record_count
        if scenario_record_count
        else 0.0
    )
    structured_record_coverage = (
        structured_record_count / scenario_record_count
        if scenario_record_count
        else 0.0
    )

    findings.append(
        EvaluationFinding(
            name="Chat requests succeeded",
            passed=error_count == 0,
            detail=f"errors={error_count}",
            recommendation=(
                "Stabilize the /chat path before tuning memory behavior."
                if error_count > 0
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Scenario messages were persisted",
            passed=(
                scenario_message_count
                >= len(dialog_step_results) * EXPECTED_MESSAGE_MULTIPLIER
            ),
            detail=f"scenario_messages={scenario_message_count}",
            recommendation=(
                "Inspect message persistence for stable conversation_id writes."
                if (
                    scenario_message_count
                    < len(dialog_step_results) * EXPECTED_MESSAGE_MULTIPLIER
                )
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Dialog Recorder produced records",
            passed=scenario_record_count > 0,
            detail=f"scenario_observations={scenario_record_count}",
            recommendation=(
                "Dialog Recorder did not compress the scenario into semantic memory."
                if scenario_record_count == 0
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Dialog Recorder catches up during reader pause",
            passed=(
                state.first_record_elapsed_seconds is not None
                and (
                    state.first_record_elapsed_seconds
                    <= MAX_POST_CHAT_FIRST_OBSERVATION_SECONDS
                )
            ),
            detail=(
                "first_record_after_final_answer="
                f"{state.first_record_elapsed_seconds:.1f}s"
                if state.first_record_elapsed_seconds is not None
                else "first_record_after_final_answer=missing"
            ),
            recommendation=(
                "Dialog Recorder is too slow after the final answer. Improve post-chat "
                "background scheduling or reduce dialog recorder workload."
                if state.first_record_elapsed_seconds is None
                or (
                    state.first_record_elapsed_seconds
                    > MAX_POST_CHAT_FIRST_OBSERVATION_SECONDS
                )
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Record provenance is present",
            passed=(record_provenance_coverage >= MIN_OBSERVATION_PROVENANCE_COVERAGE),
            detail=(f"provenance_coverage={record_provenance_coverage:.2f}"),
            recommendation=(
                "Record traceability is too weak. Improve message->record "
                "linking or provenance preservation."
                if record_provenance_coverage < MIN_OBSERVATION_PROVENANCE_COVERAGE
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Records keep structured detail",
            passed=(structured_record_coverage >= MIN_STRUCTURED_OBSERVATION_COVERAGE),
            detail=(f"structured_coverage={structured_record_coverage:.2f}"),
            recommendation=(
                "Dialog Recorder compression is shallow. Improve key_facts and "
                "action_items quality."
                if structured_record_coverage < MIN_STRUCTURED_OBSERVATION_COVERAGE
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Distiller produced scenario summaries",
            passed=(not summary_threshold_reached or scenario_summary_count > 0),
            detail=(
                f"scenario_distillations={scenario_summary_count}, "
                "unreflected_observations="
                f"{state.unreflected_record_count}, "
                "scenario_unreflected_observations="
                f"{state.scenario_unreflected_record_count}"
            ),
            recommendation=(
                "No scenario summaries were generated. Lower the distiller "
                "threshold, "
                "run more scenario dialog steps, or review distiller scheduling."
                if summary_threshold_reached and scenario_summary_count == 0
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Summary relations use the current vocabulary",
            passed=(
                scenario_summary_count == 0
                or valid_relation_summary_count == scenario_summary_count
            ),
            detail=(
                "valid_relation_distillations="
                f"{valid_relation_summary_count}/{scenario_summary_count}"
            ),
            recommendation=(
                "Distiller returned invalid relation labels. Tighten the relation "
                "prompt "
                "or output validation."
                if scenario_summary_count > 0
                and valid_relation_summary_count != scenario_summary_count
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Final long-term recall keeps the new preference active",
            passed=response_confirms_current_long_term_style(last_response),
            detail=f"final_response={last_response[:120]!r}",
            recommendation=(
                "Final recall did not keep the new long-term preference clearly active."
                if not response_confirms_current_long_term_style(last_response)
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Final long-term recall is not stuck on the old preference",
            passed=not response_has_old_style_as_active_long_term(last_response),
            detail=f"final_response={last_response[:120]!r}",
            recommendation=(
                "Old preference markers still dominate the final long-term recall. "
                "Review conflict handling or recency weighting."
                if response_has_old_style_as_active_long_term(last_response)
                else ""
            ),
        )
    )
    findings.append(
        EvaluationFinding(
            name="Global memory quality remains healthy",
            passed=quality_score >= MIN_GLOBAL_QUALITY_SCORE,
            detail=f"quality_score={quality_score}",
            recommendation=(
                "Global memory quality is low. Inspect /admin/memory/quality issues "
                "before tuning individual prompts."
                if quality_score < MIN_GLOBAL_QUALITY_SCORE
                else ""
            ),
        )
    )

    if summary_threshold_reached and scenario_summary_count > 1:
        has_transition_relation = bool(
            relation_variety.intersection({"conflicts", "outdates"})
        )
        findings.append(
            EvaluationFinding(
                name="Summary graph captures preference transitions",
                passed=has_transition_relation,
                detail=f"relation_variety={sorted(relation_variety)}",
                recommendation=(
                    "Distiller still treats preference changes as pure support. "
                    "Strengthen supports/conflicts/outdates instructions."
                    if not has_transition_relation
                    else ""
                ),
            )
        )
    else:
        findings.append(
            EvaluationFinding(
                name="Summary graph captures preference transitions",
                passed=not summary_threshold_reached,
                detail=(
                    "Not enough scenario summaries to assess transition handling. "
                    f"relation_variety={sorted(relation_variety)}, "
                    "unreflected_observations="
                    f"{state.unreflected_record_count}, "
                    "scenario_unreflected_observations="
                    f"{state.scenario_unreflected_record_count}"
                ),
                recommendation=(
                    "Run additional scenario iterations or lower the summary "
                    "threshold "
                    "before judging supports/conflicts/outdates quality."
                    if summary_threshold_reached
                    else ""
                ),
            )
        )

    return findings


def build_recommendations(findings: list[EvaluationFinding]) -> list[str]:
    """Collapse failing findings into unique actionable recommendations."""
    recommendations: list[str] = []
    for finding in findings:
        if (
            not finding.passed
            and finding.recommendation
            and finding.recommendation not in recommendations
        ):
            recommendations.append(finding.recommendation)
    return recommendations


def evaluate_scenario(
    scenario_tag: str,
    conversation_id: str,
    state: ScenarioState,
    dialog_step_results: list[ChatDialogStepResult],
) -> ScenarioReport:
    """Evaluate one scenario iteration and produce a normalized report."""
    findings = build_findings(state, dialog_step_results)
    recommendations = build_recommendations(findings)
    passed = all(finding.passed for finding in findings)
    return ScenarioReport(
        scenario_tag=scenario_tag,
        conversation_id=conversation_id,
        passed=passed,
        findings=findings,
        recommendations=recommendations,
        dialog_step_results=dialog_step_results,
        quality_score=int(state.quality_report.get("quality_score", 0)),
        scenario_counts={
            "messages": len(state.messages),
            "records": len(state.records),
            "summaries": len(state.summaries),
        },
        duplication_report=state.duplication_report
        or {
            "duplicate_observation_count": 0,
            "distillation_merge_count": 0,
            "stored_distillation_count": 0,
        },
        contamination_report=state.contamination_report
        or {
            "long_term_summary_count": 0.0,
            "contaminated_long_term_summary_count": 0.0,
            "contaminated_long_term_summary_share": 0.0,
        },
        retrieval_report=build_retrieval_report(dialog_step_results),
        phase_breakdown=build_phase_breakdown(dialog_step_results),
        first_record_elapsed_seconds=state.first_record_elapsed_seconds,
        stabilization_elapsed_seconds=state.stabilization_elapsed_seconds,
    )
