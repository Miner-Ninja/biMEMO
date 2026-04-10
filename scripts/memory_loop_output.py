"""CLI output and serialization helpers for the memory loop harness."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from scripts.memory_loop_constants import PHASE_NAMES


if TYPE_CHECKING:
    from scripts.memory_loop_models import ScenarioReport


LONG_TERM_SUMMARY_COUNT_KEY = "long_term_summary_count"
CONTAMINATED_LONG_TERM_SUMMARY_COUNT_KEY = "contaminated_long_term_summary_count"
CONTAMINATED_LONG_TERM_SUMMARY_SHARE_KEY = "contaminated_long_term_summary_share"


def _contamination_metric(
    report: ScenarioReport,
    key: str,
) -> float:
    """Return one contamination metric with a zero default."""
    return float(report.contamination_report.get(key, 0.0))


def _summarize_contamination_metric(
    reports: list[ScenarioReport],
    key: str,
) -> dict[str, float]:
    """Summarize one contamination metric across many reports."""
    return _summarize_numeric_series(
        [_contamination_metric(report, key) for report in reports]
    )


def _format_long_term_contamination(report: ScenarioReport) -> str:
    """Build one compact long-term contamination summary line."""
    long_term_summary_count = _contamination_metric(
        report,
        LONG_TERM_SUMMARY_COUNT_KEY,
    )
    contaminated_summary_count = _contamination_metric(
        report,
        CONTAMINATED_LONG_TERM_SUMMARY_COUNT_KEY,
    )
    contaminated_summary_share = _contamination_metric(
        report,
        CONTAMINATED_LONG_TERM_SUMMARY_SHARE_KEY,
    )
    return (
        "Long-term contamination: "
        f"long_term_summaries={long_term_summary_count:.0f}, "
        f"contaminated={contaminated_summary_count:.0f}, "
        f"share={contaminated_summary_share:.2f}"
    )


def _format_late_retrieval_reuse(report: ScenarioReport) -> str:
    """Build one compact late-phase retrieval reuse summary line."""
    return (
        "Late retrieval reuse: "
        "unique_summaries="
        f"{report.retrieval_report['late_phase_unique_retrieved_memory_summaries']}, "
        "dominant_hits="
        f"{report.retrieval_report['late_phase_dominant_summary_hits']}, "
        "dominant_share="
        f"{report.retrieval_report['late_phase_dominant_summary_share']:.2f}"
    )


def print_report(report: ScenarioReport) -> None:
    """Print one scenario report in a readable CLI format."""
    print("\n" + "=" * 72)
    print(f"Scenario: {report.scenario_tag}")
    print(f"Conversation ID: {report.conversation_id}")
    print(
        "Counts: "
        f"messages={report.scenario_counts['messages']}, "
        f"records={report.scenario_counts['records']}, "
        f"summaries={report.scenario_counts['summaries']}"
    )
    print(f"Global quality score: {report.quality_score}")
    print(
        "Duplication signals: "
        "duplicate_observations="
        f"{report.duplication_report['duplicate_observation_count']}, "
        "distillation_merges="
        f"{report.duplication_report['distillation_merge_count']}, "
        "stored_distillations="
        f"{report.duplication_report['stored_distillation_count']}"
    )
    print(_format_long_term_contamination(report))
    if report.retrieval_report:
        print(
            "Retrieval reuse: "
            "unique_summaries="
            f"{report.retrieval_report['unique_retrieved_memory_summaries']}, "
            "dominant_hits="
            f"{report.retrieval_report['dominant_summary_hits']}, "
            "dominant_share="
            f"{report.retrieval_report['dominant_summary_share']:.2f}"
        )
        print(_format_late_retrieval_reuse(report))
    if report.first_record_elapsed_seconds is not None:
        print(
            "First record after final answer: "
            f"{report.first_record_elapsed_seconds:.1f}s"
        )
    print(
        "Post-chat memory stabilization time: "
        f"{report.stabilization_elapsed_seconds:.1f}s"
    )
    print(f"Overall result: {'PASS' if report.passed else 'FAIL'}")
    print("-" * 72)
    print("Phase breakdown:")
    for phase_name in PHASE_NAMES:
        phase = report.phase_breakdown[phase_name]
        print(
            f"{phase_name}: dialog_steps={phase.dialog_step_count}, "
            f"avg_latency={phase.avg_latency_seconds:.1f}s, "
            "avg_retrieved_memories="
            f"{phase.avg_retrieved_memories:.1f}, "
            "avg_retrieved_messages="
            f"{phase.avg_retrieved_messages:.1f}, "
            "current_style_rate="
            f"{phase.current_style_response_rate:.2f}, "
            "old_style_only_rate="
            f"{phase.old_style_only_response_rate:.2f}, "
            f"errors={phase.error_count}"
        )
    print("-" * 72)
    for finding in report.findings:
        status = "PASS" if finding.passed else "FAIL"
        print(f"[{status}] {finding.name}: {finding.detail}")
    if report.recommendations:
        print("-" * 72)
        print("Recommendations:")
        for recommendation in report.recommendations:
            print(f"- {recommendation}")
    print("=" * 72)


def serialize_report(report: ScenarioReport) -> dict[str, Any]:
    """Convert a report into a JSON-friendly structure."""
    return {
        "scenario_tag": report.scenario_tag,
        "conversation_id": report.conversation_id,
        "passed": report.passed,
        "quality_score": report.quality_score,
        "scenario_counts": report.scenario_counts,
        "duplication_report": report.duplication_report,
        "contamination_report": report.contamination_report,
        "retrieval_report": report.retrieval_report,
        "phase_breakdown": {
            phase_name: asdict(phase_summary)
            for phase_name, phase_summary in report.phase_breakdown.items()
        },
        "first_record_elapsed_seconds": report.first_record_elapsed_seconds,
        "stabilization_elapsed_seconds": report.stabilization_elapsed_seconds,
        "findings": [asdict(finding) for finding in report.findings],
        "recommendations": report.recommendations,
        "dialog_step_results": [
            asdict(dialog_step_result)
            for dialog_step_result in report.dialog_step_results
        ],
    }


def _summarize_numeric_series(values: list[float | int]) -> dict[str, float]:
    """Return min, max, and average for one numeric report series."""
    if not values:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "spread": 0.0}
    series = [float(value) for value in values]
    minimum = min(series)
    maximum = max(series)
    return {
        "min": minimum,
        "max": maximum,
        "avg": sum(series) / len(series),
        "spread": maximum - minimum,
    }


def summarize_reports(reports: list[ScenarioReport]) -> dict[str, Any]:
    """Build a compact summary for a multi-run scenario series."""
    if not reports:
        return {
            "iterations": 0,
            "passed_iterations": 0,
            "pass_rate": 0.0,
            "records": _summarize_numeric_series([]),
            "summaries": _summarize_numeric_series([]),
            "quality_score": _summarize_numeric_series([]),
            "duplicate_observation_count": _summarize_numeric_series([]),
            "distillation_merge_count": _summarize_numeric_series([]),
            "stored_distillation_count": _summarize_numeric_series([]),
            "long_term_summary_count": _summarize_numeric_series([]),
            "contaminated_long_term_summary_count": _summarize_numeric_series([]),
            "contaminated_long_term_summary_share": _summarize_numeric_series([]),
            "unique_retrieved_memory_summaries": _summarize_numeric_series([]),
            "dominant_summary_hits": _summarize_numeric_series([]),
            "dominant_summary_share": _summarize_numeric_series([]),
            "late_phase_unique_retrieved_memory_summaries": (
                _summarize_numeric_series([])
            ),
            "late_phase_dominant_summary_hits": _summarize_numeric_series([]),
            "late_phase_dominant_summary_share": _summarize_numeric_series([]),
            "first_record_elapsed_seconds": _summarize_numeric_series([]),
            "stabilization_elapsed_seconds": _summarize_numeric_series([]),
            "phase_breakdown": {
                phase_name: {
                    "avg_retrieved_memories": _summarize_numeric_series([]),
                    "current_style_response_rate": _summarize_numeric_series([]),
                    "old_style_only_response_rate": _summarize_numeric_series([]),
                    "avg_latency_seconds": _summarize_numeric_series([]),
                }
                for phase_name in PHASE_NAMES
            },
        }

    first_record_values = [
        report.first_record_elapsed_seconds
        for report in reports
        if report.first_record_elapsed_seconds is not None
    ]
    passed_iterations = sum(1 for report in reports if report.passed)
    return {
        "iterations": len(reports),
        "passed_iterations": passed_iterations,
        "pass_rate": passed_iterations / len(reports),
        "records": _summarize_numeric_series(
            [report.scenario_counts["records"] for report in reports]
        ),
        "summaries": _summarize_numeric_series(
            [report.scenario_counts["summaries"] for report in reports]
        ),
        "quality_score": _summarize_numeric_series(
            [report.quality_score for report in reports]
        ),
        "duplicate_observation_count": _summarize_numeric_series(
            [
                report.duplication_report["duplicate_observation_count"]
                for report in reports
            ]
        ),
        "distillation_merge_count": _summarize_numeric_series(
            [
                report.duplication_report["distillation_merge_count"]
                for report in reports
            ]
        ),
        "stored_distillation_count": _summarize_numeric_series(
            [
                report.duplication_report["stored_distillation_count"]
                for report in reports
            ]
        ),
        "long_term_summary_count": _summarize_contamination_metric(
            reports,
            LONG_TERM_SUMMARY_COUNT_KEY,
        ),
        "contaminated_long_term_summary_count": _summarize_contamination_metric(
            reports,
            CONTAMINATED_LONG_TERM_SUMMARY_COUNT_KEY,
        ),
        "contaminated_long_term_summary_share": _summarize_contamination_metric(
            reports,
            CONTAMINATED_LONG_TERM_SUMMARY_SHARE_KEY,
        ),
        "unique_retrieved_memory_summaries": _summarize_numeric_series(
            [
                report.retrieval_report.get("unique_retrieved_memory_summaries", 0)
                for report in reports
            ]
        ),
        "dominant_summary_hits": _summarize_numeric_series(
            [
                report.retrieval_report.get("dominant_summary_hits", 0)
                for report in reports
            ]
        ),
        "dominant_summary_share": _summarize_numeric_series(
            [
                report.retrieval_report.get("dominant_summary_share", 0.0)
                for report in reports
            ]
        ),
        "late_phase_unique_retrieved_memory_summaries": _summarize_numeric_series(
            [
                report.retrieval_report.get(
                    "late_phase_unique_retrieved_memory_summaries",
                    0,
                )
                for report in reports
            ]
        ),
        "late_phase_dominant_summary_hits": _summarize_numeric_series(
            [
                report.retrieval_report.get("late_phase_dominant_summary_hits", 0)
                for report in reports
            ]
        ),
        "late_phase_dominant_summary_share": _summarize_numeric_series(
            [
                report.retrieval_report.get("late_phase_dominant_summary_share", 0.0)
                for report in reports
            ]
        ),
        "first_record_elapsed_seconds": _summarize_numeric_series(first_record_values),
        "stabilization_elapsed_seconds": _summarize_numeric_series(
            [report.stabilization_elapsed_seconds for report in reports]
        ),
        "phase_breakdown": {
            phase_name: {
                "avg_retrieved_memories": _summarize_numeric_series(
                    [
                        report.phase_breakdown[phase_name].avg_retrieved_memories
                        for report in reports
                    ]
                ),
                "current_style_response_rate": _summarize_numeric_series(
                    [
                        report.phase_breakdown[phase_name].current_style_response_rate
                        for report in reports
                    ]
                ),
                "old_style_only_response_rate": _summarize_numeric_series(
                    [
                        report.phase_breakdown[phase_name].old_style_only_response_rate
                        for report in reports
                    ]
                ),
                "avg_latency_seconds": _summarize_numeric_series(
                    [
                        report.phase_breakdown[phase_name].avg_latency_seconds
                        for report in reports
                    ]
                ),
            }
            for phase_name in PHASE_NAMES
        },
    }


def print_series_summary(summary: dict[str, Any]) -> None:
    """Print one compact summary block for a multi-run test series."""
    print("\n" + "=" * 72)
    print("Series summary")
    print("=" * 72)
    print(
        "Pass rate: "
        f"{summary['passed_iterations']}/{summary['iterations']} "
        f"({summary['pass_rate'] * 100:.0f}%)"
    )
    print(
        "Records: "
        f"min={summary['records']['min']:.0f}, "
        f"max={summary['records']['max']:.0f}, "
        f"avg={summary['records']['avg']:.1f}, "
        f"spread={summary['records']['spread']:.0f}"
    )
    print(
        "Summaries: "
        f"min={summary['summaries']['min']:.0f}, "
        f"max={summary['summaries']['max']:.0f}, "
        f"avg={summary['summaries']['avg']:.1f}, "
        f"spread={summary['summaries']['spread']:.0f}"
    )
    print(
        "Quality score: "
        f"min={summary['quality_score']['min']:.0f}, "
        f"max={summary['quality_score']['max']:.0f}, "
        f"avg={summary['quality_score']['avg']:.1f}, "
        f"spread={summary['quality_score']['spread']:.0f}"
    )
    print(
        "Duplicate observations: "
        f"min={summary['duplicate_observation_count']['min']:.0f}, "
        f"max={summary['duplicate_observation_count']['max']:.0f}, "
        f"avg={summary['duplicate_observation_count']['avg']:.1f}, "
        f"spread={summary['duplicate_observation_count']['spread']:.0f}"
    )
    print(
        "Distillation merges: "
        f"min={summary['distillation_merge_count']['min']:.0f}, "
        f"max={summary['distillation_merge_count']['max']:.0f}, "
        f"avg={summary['distillation_merge_count']['avg']:.1f}, "
        f"spread={summary['distillation_merge_count']['spread']:.0f}"
    )
    print(
        "Stored distillations: "
        f"min={summary['stored_distillation_count']['min']:.0f}, "
        f"max={summary['stored_distillation_count']['max']:.0f}, "
        f"avg={summary['stored_distillation_count']['avg']:.1f}, "
        f"spread={summary['stored_distillation_count']['spread']:.0f}"
    )
    print(
        "Long-term summaries: "
        f"min={summary['long_term_summary_count']['min']:.0f}, "
        f"max={summary['long_term_summary_count']['max']:.0f}, "
        f"avg={summary['long_term_summary_count']['avg']:.1f}, "
        f"spread={summary['long_term_summary_count']['spread']:.0f}"
    )
    print(
        "Contaminated long-term summaries: "
        f"min={summary['contaminated_long_term_summary_count']['min']:.0f}, "
        f"max={summary['contaminated_long_term_summary_count']['max']:.0f}, "
        f"avg={summary['contaminated_long_term_summary_count']['avg']:.1f}, "
        "spread="
        f"{summary['contaminated_long_term_summary_count']['spread']:.0f}"
    )
    print(
        "Contaminated long-term summary share: "
        f"min={summary['contaminated_long_term_summary_share']['min']:.2f}, "
        f"max={summary['contaminated_long_term_summary_share']['max']:.2f}, "
        f"avg={summary['contaminated_long_term_summary_share']['avg']:.2f}, "
        "spread="
        f"{summary['contaminated_long_term_summary_share']['spread']:.2f}"
    )
    print(
        "Unique retrieved summaries: "
        f"min={summary['unique_retrieved_memory_summaries']['min']:.0f}, "
        f"max={summary['unique_retrieved_memory_summaries']['max']:.0f}, "
        f"avg={summary['unique_retrieved_memory_summaries']['avg']:.1f}, "
        f"spread={summary['unique_retrieved_memory_summaries']['spread']:.0f}"
    )
    print(
        "Dominant retrieved summary hits: "
        f"min={summary['dominant_summary_hits']['min']:.0f}, "
        f"max={summary['dominant_summary_hits']['max']:.0f}, "
        f"avg={summary['dominant_summary_hits']['avg']:.1f}, "
        f"spread={summary['dominant_summary_hits']['spread']:.0f}"
    )
    print(
        "Dominant retrieved summary share: "
        f"min={summary['dominant_summary_share']['min']:.2f}, "
        f"max={summary['dominant_summary_share']['max']:.2f}, "
        f"avg={summary['dominant_summary_share']['avg']:.2f}, "
        f"spread={summary['dominant_summary_share']['spread']:.2f}"
    )
    print(
        "Late unique retrieved summaries: "
        f"min={summary['late_phase_unique_retrieved_memory_summaries']['min']:.0f}, "
        f"max={summary['late_phase_unique_retrieved_memory_summaries']['max']:.0f}, "
        "avg="
        f"{summary['late_phase_unique_retrieved_memory_summaries']['avg']:.1f}, "
        "spread="
        f"{summary['late_phase_unique_retrieved_memory_summaries']['spread']:.0f}"
    )
    print(
        "Late dominant summary hits: "
        f"min={summary['late_phase_dominant_summary_hits']['min']:.0f}, "
        f"max={summary['late_phase_dominant_summary_hits']['max']:.0f}, "
        f"avg={summary['late_phase_dominant_summary_hits']['avg']:.1f}, "
        f"spread={summary['late_phase_dominant_summary_hits']['spread']:.0f}"
    )
    print(
        "Late dominant summary share: "
        f"min={summary['late_phase_dominant_summary_share']['min']:.2f}, "
        f"max={summary['late_phase_dominant_summary_share']['max']:.2f}, "
        f"avg={summary['late_phase_dominant_summary_share']['avg']:.2f}, "
        f"spread={summary['late_phase_dominant_summary_share']['spread']:.2f}"
    )
    print(
        "First record after final answer: "
        f"min={summary['first_record_elapsed_seconds']['min']:.1f}s, "
        f"max={summary['first_record_elapsed_seconds']['max']:.1f}s, "
        f"avg={summary['first_record_elapsed_seconds']['avg']:.1f}s, "
        "spread="
        f"{summary['first_record_elapsed_seconds']['spread']:.1f}s"
    )
    print(
        "Settled time: "
        f"min={summary['stabilization_elapsed_seconds']['min']:.1f}s, "
        f"max={summary['stabilization_elapsed_seconds']['max']:.1f}s, "
        f"avg={summary['stabilization_elapsed_seconds']['avg']:.1f}s, "
        f"spread={summary['stabilization_elapsed_seconds']['spread']:.1f}s"
    )
    print("Phase stability:")
    for phase_name in PHASE_NAMES:
        phase_summary = summary["phase_breakdown"][phase_name]
        print(
            f"{phase_name}: "
            "retrieved_memories_avg="
            f"{phase_summary['avg_retrieved_memories']['avg']:.1f} "
            "(spread="
            f"{phase_summary['avg_retrieved_memories']['spread']:.1f}), "
            "current_style_avg="
            f"{phase_summary['current_style_response_rate']['avg']:.2f} "
            "(spread="
            f"{phase_summary['current_style_response_rate']['spread']:.2f}), "
            "old_style_only_avg="
            f"{phase_summary['old_style_only_response_rate']['avg']:.2f} "
            "(spread="
            f"{phase_summary['old_style_only_response_rate']['spread']:.2f}), "
            "latency_avg="
            f"{phase_summary['avg_latency_seconds']['avg']:.1f}s "
            "(spread="
            f"{phase_summary['avg_latency_seconds']['spread']:.1f}s)"
        )
    print("=" * 72)
