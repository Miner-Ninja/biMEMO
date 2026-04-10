"""Long-term summary contamination diagnostics for the memory loop harness."""

from __future__ import annotations

from typing import Any

from scripts.memory_loop_matchers import normalize_string_list


# Session-only phrases that should not survive in long-term summaries.
LONG_TERM_CONTAMINATION_MARKERS = (
    "memory-loop-",
    "validation run",
    "test run",
    "session-specific",
    "session specific",
    "session-only",
    "session only",
    "session identifier",
    "current session",
    "this session",
    "session end",
    "session ends",
    "session closure",
    "resets upon closure",
    "resets when the session ends",
)
LONG_TERM_SCOPE = "long_term"


def _summary_scope(summary: dict[str, Any]) -> str:
    """Return the normalized scope value from one summary record."""
    return str(summary.get("scope", summary.get("summary_scope", ""))).strip().lower()


def build_long_term_contamination_report(
    summaries: list[dict[str, Any]],
) -> dict[str, float]:
    """Measure session-only leakage inside long-term summaries."""
    long_term_summaries = [
        summary for summary in summaries if _summary_scope(summary) == LONG_TERM_SCOPE
    ]
    contaminated_summary_count = 0
    for summary in long_term_summaries:
        summary_text = " ".join(
            [
                str(summary.get("main_point", "")),
                " ".join(normalize_string_list(summary.get("based_on", []))),
            ]
        ).lower()
        if any(marker in summary_text for marker in LONG_TERM_CONTAMINATION_MARKERS):
            contaminated_summary_count += 1

    long_term_summary_count = len(long_term_summaries)
    contaminated_share = (
        contaminated_summary_count / long_term_summary_count
        if long_term_summary_count
        else 0.0
    )
    return {
        "long_term_summary_count": float(long_term_summary_count),
        "contaminated_long_term_summary_count": float(contaminated_summary_count),
        "contaminated_long_term_summary_share": contaminated_share,
    }
