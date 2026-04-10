"""Helpers for building source-backed evidence bundles from memory links."""

from __future__ import annotations

import re
from typing import Any

from backend.memory.evidence_chain_models import EvidenceAdminClient, EvidenceBundle
from backend.memory.memory_ranking import (
    MemoryRanker,
    RecordRankingInput,
    SummaryRankingInput,
)
from backend.memory.models import RecordRelationType, get_trust_level_score


SUPPORTS_RELATION = RecordRelationType.SUPPORTS.value
MIN_ADMIN_LIST_LIMIT = 1
QUERY_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
LOW_SIGNAL_QUERY_TOKENS = {
    "about",
    "again",
    "and",
    "are",
    "did",
    "does",
    "exactly",
    "from",
    "have",
    "into",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "you",
    "как",
    "какие",
    "каким",
    "какой",
    "мне",
    "это",
    "что",
    "где",
    "когда",
    "mane",
    "kaip",
    "kur",
    "ką",
    "kokie",
    "kokia",
}


def _dedupe_identifiers(raw_identifiers: list[str]) -> list[str]:
    """Return identifiers in first-seen order without duplicates."""
    seen_identifiers: set[str] = set()
    ordered_identifiers: list[str] = []
    for raw_identifier in raw_identifiers:
        identifier = str(raw_identifier).strip()
        if not identifier or identifier in seen_identifiers:
            continue
        seen_identifiers.add(identifier)
        ordered_identifiers.append(identifier)
    return ordered_identifiers


def _semantic_query_tokens(text: str) -> set[str]:
    """Extract a compact semantic token set from one query-like text."""
    return {
        token
        for token in QUERY_TOKEN_RE.findall(text.casefold())
        if len(token) >= 3 and token not in LOW_SIGNAL_QUERY_TOKENS
    }


def _count_record_relations(summary: dict[str, Any], relation_name: str) -> int:
    """Count typed record relations for one relation label."""
    record_relations = summary.get("record_relations", [])
    if not isinstance(record_relations, list):
        return 0
    return sum(
        1
        for relation in record_relations
        if isinstance(relation, dict)
        and str(relation.get("relation", "")).strip() == relation_name
    )


def _summary_search_text(summary: dict[str, Any]) -> str:
    """Build the searchable text used for summary relevance scoring."""
    return " ".join(
        [
            str(summary.get("main_point", "")),
            " ".join(
                [
                    str(item).strip()
                    for item in summary.get("based_on", [])
                    if str(item).strip()
                ]
            ),
        ]
    )


def _record_search_text(record: dict[str, Any]) -> str:
    """Build the searchable text used for record relevance scoring."""
    return " ".join(
        [
            str(record.get("summary", "")),
            " ".join(
                [
                    str(item).strip()
                    for item in record.get("key_facts", [])
                    if str(item).strip()
                ]
            ),
            " ".join(
                [
                    str(item).strip()
                    for item in record.get("action_items", [])
                    if str(item).strip()
                ]
            ),
        ]
    )


def _source_message_count(
    record_ids: list[str],
    records_by_id: dict[str, dict[str, Any]],
) -> int:
    """Count source messages linked through one set of record ids."""
    return sum(
        len(record.get("source_message_ids", []))
        for record_id in record_ids
        if isinstance(
            record := records_by_id.get(record_id),
            dict,
        )
        and isinstance(record.get("source_message_ids", []), list)
    )


def _has_full_summary_provenance(
    record_ids: list[str],
    records_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Return whether every linked record has source-message provenance."""
    return bool(record_ids) and all(
        isinstance(records_by_id.get(record_id), dict)
        and bool(records_by_id[record_id].get("source_message_ids"))
        for record_id in record_ids
    )


def _record_source_message_count(record: dict[str, Any]) -> int:
    """Count source messages attached to one evidence record."""
    if not isinstance(record.get("source_message_ids", []), list):
        return 0
    return len(record.get("source_message_ids", []))


def _compute_summary_relevance_score(
    summary: dict[str, Any],
    query_tokens: set[str],
) -> float:
    """Compute a bounded semantic relevance score for one summary."""
    if not query_tokens:
        return 0.0
    summary_tokens = _semantic_query_tokens(_summary_search_text(summary))
    if not summary_tokens:
        return 0.0
    overlap_count = len(query_tokens & summary_tokens)
    return overlap_count / max(len(query_tokens), 1)


def _compute_record_relevance_score(
    record: dict[str, Any],
    query_tokens: set[str],
) -> float:
    """Compute a bounded semantic relevance score for one evidence record."""
    if not query_tokens:
        return 0.0
    record_tokens = _semantic_query_tokens(_record_search_text(record))
    if not record_tokens:
        return 0.0
    overlap_count = len(query_tokens & record_tokens)
    return overlap_count / max(len(query_tokens), 1)


def _build_summary_ranking_input(
    summary: dict[str, Any],
    query_tokens: set[str],
    records_by_id: dict[str, dict[str, Any]],
) -> SummaryRankingInput | None:
    """Build ranking input for one summary candidate."""
    summary_id = str(summary.get("id", "")).strip()
    if not summary_id:
        return None
    linked_record_ids = extract_supporting_record_ids(summary)
    effective_trust_level_score = summary.get("effective_trust_level_score")
    if effective_trust_level_score is None:
        effective_trust_level_score = get_trust_level_score(
            str(summary.get("trust_level", "")),
        )
    revalidation_status = str(summary.get("revalidation_status", "")).strip().casefold()
    return SummaryRankingInput(
        identifier=summary_id,
        relevance_score=_compute_summary_relevance_score(summary, query_tokens),
        memory_layer=str(summary.get("scope", "long_term")).strip() or "long_term",
        linked_record_count=len(linked_record_ids),
        source_message_count=_source_message_count(linked_record_ids, records_by_id),
        has_full_provenance=_has_full_summary_provenance(
            linked_record_ids,
            records_by_id,
        ),
        created_at=str(summary.get("created_at", "")).strip(),
        trust_level_score=int(effective_trust_level_score),
        reuse_count=0,
        is_stale=revalidation_status in {"stale", "outdated"},
        is_orphaned=not linked_record_ids,
        is_duplicate=False,
        has_noise_markers=False,
        conflict_link_count=_count_record_relations(
            summary,
            RecordRelationType.CONFLICTS.value,
        ),
        outdated_link_count=_count_record_relations(
            summary,
            RecordRelationType.OUTDATES.value,
        ),
    )


def _rank_records_for_query(
    records: list[dict[str, Any]],
    query_text: str,
) -> list[dict[str, Any]]:
    """Rank evidence records for one query-aware evidence bundle."""
    query_tokens = _semantic_query_tokens(query_text)
    ranking_inputs = [
        RecordRankingInput(
            identifier=str(record.get("id", "")).strip(),
            relevance_score=_compute_record_relevance_score(record, query_tokens),
            memory_layer="long_term",
            source_message_count=_record_source_message_count(record),
            has_full_provenance=bool(record.get("source_message_ids")),
            created_at=str(record.get("created_at", "")).strip(),
            reuse_count=0,
            is_tainted=bool(record.get("tainted", False)),
            has_noise_markers=False,
            conflict_link_count=0,
            outdated_link_count=0,
        )
        for record in records
        if str(record.get("id", "")).strip()
    ]
    if not ranking_inputs:
        return records
    record_index = {
        str(record.get("id", "")).strip(): record
        for record in records
        if str(record.get("id", "")).strip()
    }
    ranked_records = MemoryRanker().rank_records(ranking_inputs)
    return [
        record_index[result.identifier]
        for result in ranked_records
        if result.identifier in record_index
    ]


def _rebuild_source_messages(
    records: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild source message order after record ranking."""
    source_message_ids = _record_source_message_ids(records)
    message_index = _entry_index_by_id(messages)
    return [
        message_index[message_id]
        for message_id in source_message_ids
        if message_id in message_index
    ]


def _record_source_message_ids(records: list[dict[str, Any]]) -> list[str]:
    """Collect deduplicated source message ids from one record list."""
    return _dedupe_identifiers(
        [
            str(message_id)
            for record in records
            for message_id in record.get("source_message_ids", [])
            if isinstance(record.get("source_message_ids", []), list)
        ]
    )


def _entry_index_by_id(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index loaded admin entries by their non-empty identifier."""
    return {
        str(entry.get("id", "")).strip(): entry
        for entry in entries
        if str(entry.get("id", "")).strip()
    }


def _entry_limit(counts: dict[str, Any], key: str) -> int:
    """Resolve one admin list limit from counted memory entries."""
    return max(int(counts.get(key, 0)), MIN_ADMIN_LIST_LIMIT)


async def _load_all_summaries(
    admin_client: EvidenceAdminClient,
    counts: dict[str, Any],
) -> list[dict[str, Any]]:
    """Load all summaries needed for one evidence lookup."""
    return await admin_client.list_summaries_full(
        limit=_entry_limit(counts, "summaries"),
        offset=0,
    )


async def _load_all_records(
    admin_client: EvidenceAdminClient,
    counts: dict[str, Any],
) -> list[dict[str, Any]]:
    """Load all records needed for one evidence lookup."""
    return await admin_client.list_records(
        limit=_entry_limit(counts, "records"),
        offset=0,
    )


async def _load_all_messages(
    admin_client: EvidenceAdminClient,
    counts: dict[str, Any],
) -> list[dict[str, Any]]:
    """Load all messages needed for one evidence lookup."""
    return await admin_client.list_messages(
        limit=_entry_limit(counts, "messages"),
        offset=0,
    )


def _summary_by_id(
    summaries: list[dict[str, Any]],
    summary_id: str,
) -> dict[str, Any] | None:
    """Return one summary by identifier from a loaded summary list."""
    return next(
        (
            summary
            for summary in summaries
            if str(summary.get("id", "")).strip() == summary_id
        ),
        None,
    )


def _ranked_evidence_bundle(
    evidence_bundle: EvidenceBundle,
    *,
    query_text: str,
    messages: list[dict[str, Any]],
) -> EvidenceBundle:
    """Rebuild one evidence bundle after query-aware record ranking."""
    ranked_records = _rank_records_for_query(
        evidence_bundle.records,
        query_text,
    )
    ranked_source_messages = _rebuild_source_messages(ranked_records, messages)
    return EvidenceBundle(
        summary=evidence_bundle.summary,
        records=ranked_records,
        source_messages=ranked_source_messages,
    )


def extract_supporting_record_ids(summary: dict[str, Any]) -> list[str]:
    """Extract record identifiers from one summary record.

    The typed ``record_relations`` field is preferred when present because it
    lets the summary distinguish support links from conflict or outdate links.
    The older ``source_record_ids`` field is used as a fallback.
    """
    record_relations = summary.get("record_relations", [])
    if isinstance(record_relations, list):
        linked_record_ids = _dedupe_identifiers(
            [
                str(relation.get("record_id", ""))
                for relation in record_relations
                if isinstance(relation, dict)
                and str(relation.get("relation", "")).strip() == SUPPORTS_RELATION
            ]
        )
        if linked_record_ids:
            return linked_record_ids

    raw_source_record_ids = summary.get("source_record_ids", [])
    if not isinstance(raw_source_record_ids, list):
        return []
    return _dedupe_identifiers([str(record_id) for record_id in raw_source_record_ids])


def build_evidence_bundle(
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> EvidenceBundle:
    """Build one evidence bundle from already loaded records and messages."""
    linked_record_ids = extract_supporting_record_ids(summary)
    record_index = _entry_index_by_id(records)
    records = [
        record_index[record_id]
        for record_id in linked_record_ids
        if record_id in record_index
    ]

    source_message_ids = _record_source_message_ids(records)
    message_index = _entry_index_by_id(messages)
    source_messages = [
        message_index[message_id]
        for message_id in source_message_ids
        if message_id in message_index
    ]

    return EvidenceBundle(
        summary=summary,
        records=records,
        source_messages=source_messages,
    )


def select_summary_for_query(
    query_text: str,
    summaries: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the best summary candidate for one evidence-style query."""
    if not summaries:
        return None

    query_tokens = _semantic_query_tokens(query_text)
    records_by_id = _entry_index_by_id(records)
    summary_index = _entry_index_by_id(summaries)
    ranking_inputs = [
        ranking_input
        for summary in summaries
        if (
            ranking_input := _build_summary_ranking_input(
                summary,
                query_tokens,
                records_by_id,
            )
        )
        is not None
    ]
    if not ranking_inputs:
        return None

    ranked_summaries = MemoryRanker().rank_summaries(ranking_inputs)
    best_identifier = ranked_summaries[0].identifier
    return summary_index.get(best_identifier)


async def load_evidence_bundle_for_summary(
    summary_id: str,
    admin_client: EvidenceAdminClient,
) -> EvidenceBundle | None:
    """Load one evidence bundle through the existing admin read paths."""
    counts = await admin_client.count_memory_entries()
    summaries = await _load_all_summaries(admin_client, counts)
    target_summary = _summary_by_id(summaries, summary_id)
    if target_summary is None:
        return None

    records = await _load_all_records(admin_client, counts)
    messages = await _load_all_messages(admin_client, counts)
    return build_evidence_bundle(target_summary, records, messages)


async def load_evidence_bundle_for_query(
    query_text: str,
    admin_client: EvidenceAdminClient,
) -> EvidenceBundle | None:
    """Load the best available evidence bundle for one user query."""
    counts = await admin_client.count_memory_entries()
    summaries = await _load_all_summaries(admin_client, counts)
    records = await _load_all_records(admin_client, counts)
    target_summary = select_summary_for_query(query_text, summaries, records)
    if target_summary is None:
        return None
    messages = await _load_all_messages(admin_client, counts)
    evidence_bundle = build_evidence_bundle(target_summary, records, messages)
    return _ranked_evidence_bundle(
        evidence_bundle,
        query_text=query_text,
        messages=messages,
    )
