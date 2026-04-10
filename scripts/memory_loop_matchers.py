"""Response and relation helpers for the memory loop evaluation harness."""

from __future__ import annotations

import re
from typing import Any

from scripts.memory_loop_constants import (
    CURRENT_PREFERENCE_MARKERS,
    LONG_TERM_SCOPE_MARKERS,
    OLD_PREFERENCE_MARKERS,
    OLD_STYLE_NEGATION_MARKERS,
    TEMPORARY_SCOPE_MARKERS,
)


def normalize_string_list(raw_value: object) -> list[str]:
    """Normalize an API field into a clean list of strings."""
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    if isinstance(raw_value, str):
        stripped_value = raw_value.strip()
        return [stripped_value] if stripped_value else []
    return []


def extract_relation_labels(summary: dict[str, Any]) -> set[str]:
    """Extract normalized relation labels from one summary record."""
    relation_labels: set[str] = set()
    for relation in summary.get("record_relations", []):
        relation_label = str(relation.get("relation", "")).strip()
        if relation_label:
            relation_labels.add(relation_label)
    return relation_labels


def extract_reflected_record_ids(summary: dict[str, Any]) -> set[str]:
    """Extract referenced record IDs from one summary record."""
    record_ids = set(normalize_string_list(summary.get("source_record_ids", [])))
    for relation in summary.get("record_relations", []):
        if not isinstance(relation, dict):
            continue
        record_id = str(relation.get("record_id", "")).strip()
        if record_id:
            record_ids.add(record_id)
    return record_ids


def response_prefers_current_style(response_text: str) -> bool:
    """Check whether a recall response reflects the updated preference."""
    lowered_text = response_text.lower()
    return any(marker in lowered_text for marker in CURRENT_PREFERENCE_MARKERS)


def response_only_mentions_old_style(response_text: str) -> bool:
    """Check whether a recall response still leans on the old preference only."""
    lowered_text = response_text.lower()
    has_old_markers = any(marker in lowered_text for marker in OLD_PREFERENCE_MARKERS)
    has_current_markers = any(
        marker in lowered_text for marker in CURRENT_PREFERENCE_MARKERS
    )
    return has_old_markers and not has_current_markers


def _split_response_segments(response_text: str) -> list[str]:
    """Split one response into short lowercase segments for focused checks."""
    normalized_text = response_text.lower().replace("\n", ".")
    return [
        segment.strip() for segment in normalized_text.split(".") if segment.strip()
    ]


def _extract_long_term_focus_segments(response_text: str) -> list[str]:
    """Extract the part of the response that describes the long-term answer."""
    paragraphs = [
        paragraph.strip().lower()
        for paragraph in response_text.split("\n\n")
        if paragraph.strip()
    ]
    focused_paragraphs: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        if not any(marker in paragraph for marker in LONG_TERM_SCOPE_MARKERS):
            continue
        focused_paragraphs.append(paragraph)
        heading_only = _is_heading_only_long_term_paragraph(paragraph)
        if heading_only and index + 1 < len(paragraphs):
            focused_paragraphs.append(paragraphs[index + 1])
    if focused_paragraphs:
        return focused_paragraphs
    return _split_response_segments(response_text)


def _response_explicitly_states_long_term_scope(response_text: str) -> bool:
    """Return True when the response names a long-term section explicitly."""
    lowered_text = response_text.lower()
    strong_markers = (
        "long-term",
        "long term",
        "default",
        "future technical",
        "future debugging",
    )
    if any(marker in lowered_text for marker in strong_markers):
        return True

    soft_markers = ("active preference", "current style")
    if any(marker in lowered_text for marker in soft_markers):
        return not any(
            temporary_marker in lowered_text
            for temporary_marker in TEMPORARY_SCOPE_MARKERS
        )
    return False


_EXCEPTION_SPLIT_RE = re.compile(
    r"\bunless\b|\bonly if\b|\bonly when explicitly requested\b|"
    r"\bunless explicitly requested\b|\bexcept when\b|"
    r"\bif you explicitly request\b",
)


def _primary_long_term_clause(segment: str) -> str:
    """Return the part of a segment before exception-style escape clauses."""
    return _EXCEPTION_SPLIT_RE.split(segment, maxsplit=1)[0].strip()


def _segment_uses_current_style_only_as_exception(segment: str) -> bool:
    """Return True when current-style wording appears only in an exception clause."""
    has_current_markers = any(
        marker in segment for marker in CURRENT_PREFERENCE_MARKERS
    )
    return has_current_markers and bool(_EXCEPTION_SPLIT_RE.search(segment))


def _is_heading_only_long_term_paragraph(paragraph: str) -> bool:
    """Return True when a paragraph is only a long-term heading label."""
    if any(marker in paragraph for marker in CURRENT_PREFERENCE_MARKERS):
        return False
    if any(marker in paragraph for marker in OLD_PREFERENCE_MARKERS):
        return False
    normalized = paragraph.rstrip(":").strip()
    word_count = len(normalized.split())
    return word_count <= 6 and any(
        marker in normalized for marker in LONG_TERM_SCOPE_MARKERS
    )


def response_has_old_style_as_active_long_term(response_text: str) -> bool:
    """Return whether the response still presents the old style as active."""
    focused_segments = _extract_long_term_focus_segments(response_text)
    negation_markers = tuple(
        marker
        for marker in OLD_STYLE_NEGATION_MARKERS
        if marker not in {"earlier", "previous"}
    )

    for segment in focused_segments:
        primary_clause = _primary_long_term_clause(segment)
        has_old_markers = any(
            marker in primary_clause for marker in OLD_PREFERENCE_MARKERS
        )
        has_current_markers = any(
            marker in primary_clause for marker in CURRENT_PREFERENCE_MARKERS
        )
        has_negation = any(marker in segment for marker in negation_markers)
        if (
            has_old_markers
            and _segment_uses_current_style_only_as_exception(segment)
            and not has_negation
        ):
            return True
        if has_old_markers and not has_current_markers and not has_negation:
            return True
    return False


def response_confirms_current_long_term_style(response_text: str) -> bool:
    """Return whether the final recall keeps the new style as active long-term."""
    if not _response_explicitly_states_long_term_scope(response_text):
        return False

    focused_segments = _extract_long_term_focus_segments(response_text)
    has_current_markers = any(
        any(marker in segment for marker in CURRENT_PREFERENCE_MARKERS)
        for segment in focused_segments
    )
    if not has_current_markers:
        return False
    return not response_has_old_style_as_active_long_term(response_text)
