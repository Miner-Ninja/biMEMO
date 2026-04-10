"""Ranking, metadata extraction, and suppression helpers for context assembly.

Provides metadata normalization for records and summaries, search-score
ranking via MemoryRanker, suppression of redundant records covered by
summaries, semantic coverage text building, and label/content formatting
for prompt entries. All functions are pure or nearly pure. Consumed by
runtime_helpers.py and runtime.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.memory.retrieval import SearchResult

from backend.memory.context_builder.runtime_models import PromptEntryBuildResult
from backend.memory.context_builder.text_processing import (
    estimate_tokens,
    normalize_identifier_list,
    normalize_structured_items,
)
from backend.memory.memory_ranking import (
    MemoryRanker,
    RecordRankingInput,
    SummaryRankingInput,
)


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------


def extract_summary_identity_fields(
    summary: tuple[str, ...],
) -> tuple[str, object, list[str], object]:
    """Extract the shared identity fields from one summary tuple."""
    if len(summary) >= 4:
        return (
            str(summary[0]),
            summary[1],
            normalize_identifier_list(summary[2]),
            summary[3],
        )
    if len(summary) >= 2:
        return "", summary[0], [], summary[1]
    return "", summary[0], [], ""


def build_summary_metadata(
    *,
    summary_identifier: str,
    main_point: object,
    source_record_ids: list[str],
    created_at: object,
    tuple_summary: tuple[str, ...] | None,
) -> dict[str, object]:
    """Build the normalized summary metadata dictionary."""
    return {
        "id": summary_identifier,
        "main_point": main_point,
        "source_record_ids": source_record_ids,
        "created_at": created_at,
        "revalidation_status": (
            tuple_summary[4]
            if tuple_summary is not None and len(tuple_summary) >= 5
            else ""
        ),
        "effective_trust_level_score": (
            int(tuple_summary[5])
            if tuple_summary is not None and len(tuple_summary) >= 6
            else 0
        ),
        "record_relations": (
            tuple_summary[6]
            if tuple_summary is not None and len(tuple_summary) >= 7
            else []
        ),
        "scope": (
            str(tuple_summary[7])
            if tuple_summary is not None and len(tuple_summary) >= 8
            else "long_term"
        ),
    }


def summary_metadata(
    summary: str | tuple[str, ...],
) -> dict[str, object]:
    """Normalize heterogeneous summary entries into one structure."""
    if not isinstance(summary, tuple):
        return build_summary_metadata(
            summary_identifier="",
            main_point=summary,
            source_record_ids=[],
            created_at="",
            tuple_summary=None,
        )

    summary_identifier, main_point, source_record_ids, created_at = (
        extract_summary_identity_fields(summary)
    )
    return build_summary_metadata(
        summary_identifier=summary_identifier,
        main_point=main_point,
        source_record_ids=source_record_ids,
        created_at=created_at,
        tuple_summary=summary,
    )


def extract_record_detail_fields(
    record: tuple[str, ...],
) -> tuple[list[str] | None, list[str] | None, str, list[str], bool]:
    """Extract the variable detail fields from one record tuple."""
    key_facts: list[str] | None = None
    action_items: list[str] | None = None
    created_at = ""
    source_message_ids: list[str] = []
    tainted = False

    if len(record) >= 7:
        return (
            record[2],
            record[3],
            str(record[4]),
            normalize_identifier_list(record[5]),
            bool(record[6]),
        )
    if len(record) >= 5:
        return record[2], record[3], str(record[4]), source_message_ids, tainted
    if len(record) >= 3 and isinstance(record[2], str):
        created_at = record[2]
    return key_facts, action_items, created_at, source_message_ids, tainted


def record_metadata(record: tuple[str, ...]) -> dict[str, object]:
    """Normalize heterogeneous record entries into one structure."""
    record_identifier = str(record[0]) if record else ""
    summary = record[1] if len(record) >= 2 else ""
    key_facts, action_items, created_at, source_message_ids, tainted = (
        extract_record_detail_fields(record)
    )
    return {
        "id": record_identifier,
        "summary": summary,
        "key_facts": key_facts,
        "action_items": action_items,
        "created_at": created_at,
        "source_message_ids": source_message_ids,
        "tainted": tainted,
        "scope": "long_term",
    }


def supported_record_ids(summary_data: dict[str, object]) -> list[str]:
    """Return linked record IDs for dedup decisions."""
    record_relations = summary_data["record_relations"]
    if isinstance(record_relations, list) and record_relations:
        supporting_ids = [
            str(link.get("record_id", ""))
            for link in record_relations
            if isinstance(link, dict)
            and str(link.get("relation", "")) == "supports"
            and str(link.get("record_id", ""))
        ]
        if supporting_ids:
            return supporting_ids
    return list(summary_data["source_record_ids"])


def should_include_reflection(
    summary: str | tuple[str, ...],
    memory_query: bool,
) -> bool:
    """Return whether a summary should remain prompt-eligible."""
    summary_status = str(summary_metadata(summary)["revalidation_status"])
    return summary_status not in {"outdated", "conflicted"}


# ---------------------------------------------------------------------------
# Build / label helpers
# ---------------------------------------------------------------------------


def build_record_content(
    summary: str,
    key_facts: object = None,
    action_items: object = None,
) -> str:
    """Build prompt text for an record from structured fields."""
    lines = [summary]
    normalized_key_facts = normalize_structured_items(key_facts)
    normalized_action_items = normalize_structured_items(action_items)

    if normalized_key_facts:
        lines.append("Key facts:")
        lines.extend(f"- {fact}" for fact in normalized_key_facts)
    if normalized_action_items:
        lines.append("Action items:")
        lines.extend(f"- {item}" for item in normalized_action_items)
    return "\n".join(lines)


def build_record_label(
    *,
    record_text: str,
    is_matched: bool,
    date_str: str,
) -> str:
    """Build the labeled prompt text for one record."""
    if is_matched and date_str:
        return f"[Retrieved memory, {date_str}] {record_text}"
    if is_matched:
        return f"[Retrieved memory] {record_text}"
    if date_str:
        return f"[{date_str}] {record_text}"
    return record_text


def build_summary_label(
    *,
    text: str,
    date_str: str,
    is_matched: bool,
) -> str:
    """Build the labeled prompt text for one summary."""
    if is_matched and date_str:
        return f"[Retrieved summary, {date_str}] {text}"
    if is_matched:
        return f"[Retrieved summary] {text}"
    if date_str:
        return f"[{date_str}] {text}"
    return text


def build_retrieved_message_text(
    *,
    message_content: str,
    conversation_identifier: str,
    session_id: str | None,
    message_date: str,
) -> str:
    """Build the labeled prompt text for one retrieved message."""
    if session_id and conversation_identifier == session_id:
        if message_date:
            return f"[Earlier in conversation, {message_date}] {message_content}"
        return f"[Earlier in conversation] {message_content}"

    if message_date:
        return f"[Retrieved message, {message_date}] {message_content}"
    return f"[Retrieved message] {message_content}"


# ---------------------------------------------------------------------------
# Record prompt entry builder
# ---------------------------------------------------------------------------


def is_suppressed_record(
    record: tuple[str, ...],
    suppressed_record_ids: set[str],
) -> bool:
    """Return True when this record is already covered by matched summaries."""
    return str(record[0]) in suppressed_record_ids


def build_record_prompt_entry(
    *,
    record: tuple[str, ...],
    matched_record_ids: set[str],
    format_date: Callable[[str], str],
) -> PromptEntryBuildResult:
    """Build one labeled record prompt entry and its accounting data."""
    record_identifier = str(record[0])
    summary = record[1]
    key_facts = None
    action_items = None
    if len(record) >= 5:
        key_facts = record[2]
        action_items = record[3]
        date_str = format_date(record[4]) if record[4] else ""
    else:
        date_str = format_date(record[2]) if len(record) > 2 else ""

    is_matched = record_identifier in matched_record_ids
    record_text = build_record_content(
        summary,
        key_facts=key_facts,
        action_items=action_items,
    )
    text = build_record_label(
        record_text=record_text,
        is_matched=is_matched,
        date_str=date_str,
    )
    return PromptEntryBuildResult(
        content=text,
        cost=estimate_tokens(text),
        is_matched=is_matched,
        text=str(summary),
        date_str=date_str,
    )


# ---------------------------------------------------------------------------
# Summary prompt entry builder
# ---------------------------------------------------------------------------


def build_summary_prompt_entry(
    *,
    summary: str | tuple[str, ...],
    matched_summary_ids: set[str],
    format_date: Callable[[str], str],
) -> PromptEntryBuildResult:
    """Build one labeled summary prompt entry and its accounting data."""
    summary_data = summary_metadata(summary)
    summary_identifier = str(summary_data["id"])
    text = str(summary_data["main_point"])
    date_str = format_date(str(summary_data["created_at"]))
    content = build_summary_label(
        text=text,
        date_str=date_str,
        is_matched=bool(
            summary_identifier and summary_identifier in matched_summary_ids
        ),
    )
    return PromptEntryBuildResult(
        content=content,
        cost=estimate_tokens(content),
        is_matched=bool(
            summary_identifier and summary_identifier in matched_summary_ids
        ),
        text=text,
        date_str=date_str,
        identifier=summary_identifier,
    )


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------


def build_search_score_map(
    search_results: list[SearchResult],
) -> dict[str, float]:
    """Build a search score lookup map by identifier."""
    return {
        str(search_result.identifier): search_result.score
        for search_result in search_results
    }


def build_record_source_message_count_map(
    records: list[tuple[str, ...]],
) -> dict[str, int]:
    """Build record-to-source-message-count lookup for summary ranking."""
    return {
        str(record_data["id"]): len(list(record_data["source_message_ids"]))
        for record in records
        if (record_data := record_metadata(record))["id"]
    }


def build_record_ranking_input(
    *,
    record_identifier: str,
    score: float,
    record_data: dict[str, object],
) -> RecordRankingInput:
    """Build one ranking input for a matched record."""
    source_message_ids = list(record_data["source_message_ids"])
    return RecordRankingInput(
        identifier=record_identifier,
        relevance_score=score,
        memory_layer=str(record_data["scope"]).strip() or "long_term",
        source_message_count=len(source_message_ids),
        has_full_provenance=bool(source_message_ids),
        created_at=str(record_data["created_at"]),
        reuse_count=0,
        is_tainted=bool(record_data["tainted"]),
        has_noise_markers=False,
        conflict_link_count=0,
        outdated_link_count=0,
    )


def rank_record_matches(
    matched_inputs: list[RecordRankingInput],
) -> list:
    """Rank matched record inputs with the shared memory ranker."""
    return MemoryRanker().rank_records(matched_inputs)


def collect_ranked_record_inputs(
    records: list[tuple[str, ...]],
    search_results: list[SearchResult],
) -> tuple[
    list[RecordRankingInput],
    dict[str, tuple[str, ...]],
    set[str],
    list[tuple[str, ...]],
]:
    """Collect ranking inputs and indexes for matched record search hits."""
    score_map = build_search_score_map(search_results)
    matched_inputs: list[RecordRankingInput] = []
    matched_record_index: dict[str, tuple[str, ...]] = {}
    matched_ids: set[str] = set()
    unmatched: list[tuple[str, ...]] = []

    for record in records:
        record_data = record_metadata(record)
        record_identifier = str(record_data["id"])
        score = score_map.get(record_identifier)
        if score is None:
            unmatched.append(record)
            continue
        matched_inputs.append(
            build_record_ranking_input(
                record_identifier=record_identifier,
                score=score,
                record_data=record_data,
            )
        )
        matched_record_index[record_identifier] = record
        matched_ids.add(record_identifier)

    return matched_inputs, matched_record_index, matched_ids, unmatched


def rank_observations_by_id(
    records: list[tuple[str, ...]],
    search_results: list[SearchResult] | None,
) -> tuple[list[tuple[str, ...]], set[str]]:
    """Rank record tuples by search score and record quality.

    Matched records are sorted by memory rank,
    followed by unmatched records in original order.

    Args:
        records: List of (id, summary[, created_at]) tuples.
        search_results: Ranked search results (may be None).

    Returns:
        Tuple of (reordered records, set of matched IDs).
    """
    if not search_results:
        return records, set()

    matched_inputs, matched_record_index, matched_ids, unmatched = (
        collect_ranked_record_inputs(records, search_results)
    )
    ranked_matches = rank_record_matches(matched_inputs)
    ranked_records = [
        matched_record_index[result.identifier]
        for result in ranked_matches
        if result.identifier in matched_record_index
    ]
    return ranked_records + unmatched, matched_ids


def summary_relation_counts(record_relations: object) -> tuple[int, int, int]:
    """Count support, conflict, and outdate links for one summary."""
    if not isinstance(record_relations, list):
        return 0, 0, 0
    supports_count = sum(
        1
        for relation in record_relations
        if isinstance(relation, dict)
        and str(relation.get("relation", "")) == "supports"
    )
    conflict_count = sum(
        1
        for relation in record_relations
        if isinstance(relation, dict)
        and str(relation.get("relation", "")) == "conflicts"
    )
    outdated_count = sum(
        1
        for relation in record_relations
        if isinstance(relation, dict)
        and str(relation.get("relation", "")) == "outdates"
    )
    return supports_count, conflict_count, outdated_count


def build_summary_ranking_input(
    *,
    summary_identifier: str,
    score: float,
    summary_data: dict[str, object],
    record_source_message_count_by_id: dict[str, int],
) -> SummaryRankingInput:
    """Build one ranking input for a matched summary."""
    supports_count, conflict_count, outdated_count = summary_relation_counts(
        summary_data["record_relations"]
    )
    linked_record_ids = supported_record_ids(summary_data)
    source_message_count = sum(
        record_source_message_count_by_id.get(record_identifier, 0)
        for record_identifier in linked_record_ids
    )
    has_full_provenance = bool(linked_record_ids) and all(
        record_source_message_count_by_id.get(record_identifier, 0) > 0
        for record_identifier in linked_record_ids
    )
    revalidation_status = str(summary_data["revalidation_status"]).casefold()
    return SummaryRankingInput(
        identifier=summary_identifier,
        relevance_score=score,
        memory_layer=str(summary_data["scope"]).strip() or "long_term",
        linked_record_count=supports_count,
        source_message_count=source_message_count,
        has_full_provenance=has_full_provenance,
        created_at=str(summary_data["created_at"]),
        trust_level_score=int(summary_data["effective_trust_level_score"]),
        reuse_count=0,
        is_stale=revalidation_status in {"stale", "outdated"},
        is_orphaned=not has_full_provenance,
        is_duplicate=False,
        has_noise_markers=False,
        conflict_link_count=conflict_count,
        outdated_link_count=outdated_count,
    )


def rank_summary_matches(
    matched_inputs: list[SummaryRankingInput],
) -> list:
    """Rank matched summary inputs with the shared memory ranker."""
    return MemoryRanker().rank_summaries(matched_inputs)


def collect_ranked_summary_inputs(
    *,
    summaries: list[str] | list[tuple[str, ...]],
    search_results: list[SearchResult],
    records: list[tuple[str, ...]],
) -> tuple[
    list[SummaryRankingInput],
    dict[str, str | tuple[str, ...]],
    set[str],
    list[str] | list[tuple[str, ...]],
]:
    """Collect ranking inputs and indexes for matched summary search hits."""
    score_map = build_search_score_map(search_results)
    record_source_message_count_by_id = build_record_source_message_count_map(records)
    matched_inputs: list[SummaryRankingInput] = []
    matched_summary_index: dict[str, str | tuple[str, ...]] = {}
    matched_ids: set[str] = set()
    unmatched: list[str] | list[tuple[str, ...]] = []

    for summary in summaries:
        summary_data = summary_metadata(summary)
        summary_identifier = str(summary_data["id"])
        if not summary_identifier:
            unmatched.append(summary)
            continue
        score = score_map.get(summary_identifier)
        if score is None:
            unmatched.append(summary)
            continue
        matched_inputs.append(
            build_summary_ranking_input(
                summary_identifier=summary_identifier,
                score=score,
                summary_data=summary_data,
                record_source_message_count_by_id=record_source_message_count_by_id,
            )
        )
        matched_summary_index[summary_identifier] = summary
        matched_ids.add(summary_identifier)

    return matched_inputs, matched_summary_index, matched_ids, unmatched


def rank_reflections_by_id(
    summaries: list[str] | list[tuple[str, ...]],
    search_results: list[SearchResult] | None,
    records: list[tuple[str, ...]],
) -> tuple[list[str] | list[tuple[str, ...]], set[str]]:
    """Rank summaries by search score when identifiers are available.

    Summaries may be plain strings, ``(main_point, created_at)``
    tuples, or enhanced ``(id, main_point, source_record_ids, created_at, ...)``
    tuples. Only the
    enhanced tuple form can be matched against search results.

    Args:
        summaries: Summary entries to rank.
        search_results: Ranked summary search results.
        records: Available record tuples for linked-record provenance checks.

    Returns:
        Tuple of (reordered summaries, matched summary IDs).
    """
    if not search_results:
        return summaries, set()

    matched_inputs, matched_summary_index, matched_ids, unmatched = (
        collect_ranked_summary_inputs(
            summaries=summaries,
            search_results=search_results,
            records=records,
        )
    )
    ranked_matches = rank_summary_matches(matched_inputs)
    ranked_summaries = [
        matched_summary_index[result.identifier]
        for result in ranked_matches
        if result.identifier in matched_summary_index
    ]
    return ranked_summaries + unmatched, matched_ids


# ---------------------------------------------------------------------------
# Suppression / dedup helpers
# ---------------------------------------------------------------------------


def compute_suppressed_record_ids(
    records: list[tuple[str, ...]],
    ranked_reflections: list[str] | list[tuple[str, ...]],
    matched_summary_ids: set[str],
    matched_record_ids: set[str],
) -> set[str]:
    """Suppress redundant matched records already covered by summaries.

    For each matched summary, keep at most one matched supporting
    record from its covered record IDs. Remaining matched
    records in that covered set are omitted from the prompt to
    reduce duplication while preserving one concrete exemplar.
    """
    record_order_index = {str(record[0]): index for index, record in enumerate(records)}
    suppressed_ids: set[str] = set()

    for summary in ranked_reflections:
        summary_data = summary_metadata(summary)
        summary_identifier = str(summary_data["id"])
        if not summary_identifier:
            continue
        if summary_identifier not in matched_summary_ids:
            continue

        covered_record_ids = [
            record_identifier
            for record_identifier in supported_record_ids(summary_data)
            if record_identifier in matched_record_ids
        ]
        if len(covered_record_ids) <= 1:
            continue

        covered_record_ids.sort(
            key=lambda record_identifier: record_order_index.get(
                record_identifier,
                len(record_order_index),
            )
        )
        suppressed_ids.update(covered_record_ids[1:])

    return suppressed_ids


def build_semantic_coverage_texts(
    *,
    records: list[tuple[str, ...]],
    ranked_reflections: list[str] | list[tuple[str, ...]],
    matched_record_ids: set[str],
    matched_summary_ids: set[str],
    suppressed_record_ids: set[str],
    memory_query: bool,
) -> list[str]:
    """Build semantic coverage text used to suppress duplicate retrieved messages."""
    if not memory_query:
        return []

    semantic_coverage_texts: list[str] = []
    for record in records:
        record_identifier = str(record[0])
        if record_identifier not in matched_record_ids:
            continue
        if record_identifier in suppressed_record_ids:
            continue
        if len(record) >= 5:
            semantic_coverage_texts.append(
                build_record_content(
                    record[1],
                    key_facts=record[2],
                    action_items=record[3],
                )
            )
        else:
            semantic_coverage_texts.append(record[1])

    for summary in ranked_reflections:
        summary_data = summary_metadata(summary)
        summary_identifier = str(summary_data["id"])
        if summary_identifier and summary_identifier in matched_summary_ids:
            semantic_coverage_texts.append(str(summary_data["main_point"]))
    return semantic_coverage_texts
