"""Summary storage operations for admin management.

Provides list, detail, and delete operations on summaries
stored in the SQLCipher database.
"""

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.memory.retrieval.fulltext import delete_summary_index


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)
DISTILLATION_AGING_WINDOW_DAYS = 30
DISTILLATION_MAX_AGING_PENALTY = 2
PARTIAL_SUPPORT_PENALTY = 1
ORPHANED_SUPPORT_PENALTY = 2
CONFLICT_PENALTY = 1
CONFLICTED_PENALTY = 2
OUTDATED_PENALTY = 3
SUPPORTS_RELATION = "supports"
CONFLICTS_RELATION = "conflicts"
OUTDATES_RELATION = "outdates"

RELATION_NORMALIZATION_MAP = {
    "supports": SUPPORTS_RELATION,
    "conflicts": CONFLICTS_RELATION,
    "outdates": OUTDATES_RELATION,
}
TRUST_LEVEL_SCORE_TO_LABEL = {
    3: "high",
    2: "medium",
    1: "low",
}
VALID_DISTILLATION_SCOPES = {"long_term", "short_term"}


def _normalize_relation_value(raw_relation: object) -> str:
    """Normalize stored relation values to the current vocabulary."""
    normalized_relation = RELATION_NORMALIZATION_MAP.get(
        str(raw_relation).strip().lower(),
    )
    if normalized_relation is not None:
        return normalized_relation
    return SUPPORTS_RELATION


def _build_in_clause_query(base_sql: str, item_count: int) -> str:
    """Build a SQL query with a validated ``IN (?, ?, ...)`` clause."""
    placeholders = ", ".join("?" for _ in range(item_count))
    return f"{base_sql} ({placeholders})"


def _parse_timestamp(timestamp: str) -> datetime | None:
    """Parse stored timestamps from SQLCipher tables."""
    if not timestamp:
        return None
    normalized_timestamp = timestamp.replace("Z", "+00:00")
    try:
        parsed_timestamp = datetime.fromisoformat(normalized_timestamp)
    except ValueError:
        return None
    if parsed_timestamp.tzinfo is None:
        return parsed_timestamp.replace(tzinfo=UTC)
    return parsed_timestamp.astimezone(UTC)


def _compute_age_penalty(created_at: str) -> int:
    """Compute an age-based trust-level penalty for a summary."""
    created_timestamp = _parse_timestamp(created_at)
    if created_timestamp is None:
        return 0
    age_days = max(0, (datetime.now(tz=UTC) - created_timestamp).days)
    return min(
        age_days // DISTILLATION_AGING_WINDOW_DAYS,
        DISTILLATION_MAX_AGING_PENALTY,
    )


def _normalize_source_record_ids(raw_value: str | list[str] | None) -> list[str]:
    """Normalize stored source record identifiers."""
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(record_id) for record_id in raw_value if record_id]
    if not raw_value:
        return []
    try:
        parsed_value = json.loads(raw_value)
    except json.JSONDecodeError:
        return [raw_value]
    if isinstance(parsed_value, list):
        return [str(record_id) for record_id in parsed_value if record_id]
    return []


def _normalize_record_relations(
    raw_value: str | list[dict[str, str]] | None,
    source_record_ids: list[str],
) -> list[dict[str, str]]:
    """Normalize typed record relations for summary provenance."""
    if isinstance(raw_value, list):
        return [
            {
                "record_id": str(link.get("record_id", "")),
                "relation": _normalize_relation_value(
                    link.get("relation", SUPPORTS_RELATION),
                ),
            }
            for link in raw_value
            if str(link.get("record_id", ""))
        ]
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed_links = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed_links = []
        if isinstance(parsed_links, list):
            normalized_links = [
                {
                    "record_id": str(link.get("record_id", "")),
                    "relation": _normalize_relation_value(
                        link.get("relation", SUPPORTS_RELATION),
                    ),
                }
                for link in parsed_links
                if isinstance(link, dict) and str(link.get("record_id", ""))
            ]
            if normalized_links:
                return normalized_links
    return [
        {
            "record_id": record_id,
            "relation": SUPPORTS_RELATION,
        }
        for record_id in source_record_ids
    ]


def _merge_source_record_ids(
    existing_ids: list[str],
    new_ids: list[str],
) -> list[str]:
    """Append new record ids while preserving stored order."""
    merged_ids = list(existing_ids)
    seen_ids = set(existing_ids)
    for record_id in new_ids:
        if record_id in seen_ids:
            continue
        merged_ids.append(record_id)
        seen_ids.add(record_id)
    return merged_ids


def _merge_record_relations(
    existing_relations: list[dict[str, str]],
    new_relations: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge typed record relations by record id.

    Existing links keep their order. If the same record appears again,
    the newer relation replaces the older one so upgraded relation labels are
    not lost.
    """
    merged_relations: list[dict[str, str]] = []
    relation_index_by_record_id: dict[str, int] = {}

    for relation in existing_relations:
        record_id = str(relation.get("record_id", ""))
        if not record_id:
            continue
        normalized_relation = {
            "record_id": record_id,
            "relation": _normalize_relation_value(
                relation.get("relation", SUPPORTS_RELATION)
            ),
        }
        relation_index_by_record_id[record_id] = len(merged_relations)
        merged_relations.append(normalized_relation)

    for relation in new_relations:
        record_id = str(relation.get("record_id", ""))
        if not record_id:
            continue
        normalized_relation = {
            "record_id": record_id,
            "relation": _normalize_relation_value(
                relation.get("relation", SUPPORTS_RELATION)
            ),
        }
        existing_index = relation_index_by_record_id.get(record_id)
        if existing_index is None:
            relation_index_by_record_id[record_id] = len(merged_relations)
            merged_relations.append(normalized_relation)
            continue
        merged_relations[existing_index] = normalized_relation

    return merged_relations


def _normalize_reason_text(reason: str) -> str:
    """Normalize one reason so near-identical text compares cleanly."""
    return " ".join(str(reason).split()).strip(" -:;,.")


def _merge_based_on_reasons(
    existing_reasons: list[str],
    new_reasons: list[str],
) -> list[str]:
    """Merge two reason lists while keeping insertion order."""
    merged_reasons: list[str] = []
    seen_reasons: set[str] = set()
    for raw_reason in [*existing_reasons, *new_reasons]:
        normalized_reason = _normalize_reason_text(raw_reason)
        if not normalized_reason:
            continue
        reason_key = normalized_reason.casefold()
        if reason_key in seen_reasons:
            continue
        merged_reasons.append(normalized_reason)
        seen_reasons.add(reason_key)
    return merged_reasons


def _text_detail_score(text: str) -> tuple[int, int]:
    """Score text detail so richer main points win merges."""
    normalized_text = " ".join(str(text).split()).strip()
    return (
        len(normalized_text.split()),
        len(normalized_text),
    )


def _choose_richer_main_point(existing_main_point: str, new_main_point: str) -> str:
    """Prefer the more detailed main point when duplicate summaries merge."""
    normalized_existing = " ".join(existing_main_point.split()).strip()
    normalized_new = " ".join(new_main_point.split()).strip()
    if not normalized_existing:
        return normalized_new
    if not normalized_new:
        return normalized_existing
    if _text_detail_score(normalized_new) > _text_detail_score(normalized_existing):
        return normalized_new
    return normalized_existing


def _normalize_trust_level_label(
    raw_trust_level: str,
    raw_trust_score: int,
) -> tuple[str, int]:
    """Normalize trust label and score for storage updates."""
    normalized_score = max(1, min(int(raw_trust_score), 3))
    normalized_label = TRUST_LEVEL_SCORE_TO_LABEL.get(
        normalized_score,
        str(raw_trust_level).strip().lower() or "medium",
    )
    if normalized_label not in {"high", "medium", "low"}:
        normalized_label = "medium"
    return normalized_label, normalized_score


def _normalize_summary_scope(raw_scope: str) -> str:
    """Normalize stored summary scope labels."""
    normalized_scope = str(raw_scope).strip().lower()
    if normalized_scope in VALID_DISTILLATION_SCOPES:
        return normalized_scope
    return "long_term"


def _merge_summary_scope(existing_scope: str, new_scope: str) -> str:
    """Prefer long-term scope when either duplicate carries long-lived value."""
    normalized_existing = _normalize_summary_scope(existing_scope)
    normalized_new = _normalize_summary_scope(new_scope)
    if "long_term" in {normalized_existing, normalized_new}:
        return "long_term"
    return "short_term"


def _build_record_state_map(
    db: DatabaseManager,
    source_record_ids: list[str],
) -> dict[str, bool]:
    """Load active support state for source records."""
    if not source_record_ids:
        return {}
    rows = db.execute(
        _build_in_clause_query(
            "SELECT id, tainted FROM records WHERE id IN",
            len(source_record_ids),
        ),
        tuple(source_record_ids),
    ).fetchall()
    return {row[0]: not bool(row[1]) for row in rows}


def _compute_support_penalty(
    supporting_record_ids: list[str],
    record_state_map: dict[str, bool],
) -> tuple[int, int]:
    """Compute support penalty and count active linked records."""
    if not supporting_record_ids:
        return 0, 0
    active_support_count = sum(
        1
        for record_id in supporting_record_ids
        if record_state_map.get(record_id, False)
    )
    if active_support_count == 0:
        return ORPHANED_SUPPORT_PENALTY, active_support_count
    if active_support_count < len(supporting_record_ids):
        return PARTIAL_SUPPORT_PENALTY, active_support_count
    return 0, active_support_count


def _relation_record_ids(
    record_relations: list[dict[str, str]],
    relation_name: str,
) -> list[str]:
    """Return record IDs for the requested relation."""
    return [
        str(link["record_id"])
        for link in record_relations
        if str(link.get("relation", "")) == relation_name
    ]


def _active_relation_count(
    record_ids: list[str],
    record_state_map: dict[str, bool],
) -> int:
    """Count active records in the given relation bucket."""
    return sum(1 for record_id in record_ids if record_state_map.get(record_id, False))


def _relation_penalty_and_status(
    active_support_count: int,
    supporting_record_count: int,
    active_conflict_count: int,
    active_outdate_count: int,
) -> tuple[int, str]:
    """Compute relation-aware penalty and revalidation status."""
    if active_outdate_count > 0:
        return OUTDATED_PENALTY, "outdated"
    if active_conflict_count > 0 and active_support_count == 0:
        return CONFLICTED_PENALTY, "conflicted"
    if active_conflict_count > 0:
        return CONFLICT_PENALTY, "mixed_support"
    if supporting_record_count and active_support_count == 0:
        return 0, "orphaned"
    if supporting_record_count and active_support_count < supporting_record_count:
        return 0, "partial_support"
    return 0, "fresh"


def _revalidation_status(age_penalty: int, relation_status: str) -> str:
    """Build a human-readable summary revalidation status."""
    if relation_status != "fresh":
        return relation_status
    if age_penalty > 0:
        return "stale"
    return "fresh"


def _load_distillation_rows(
    db: DatabaseManager,
    summary_ids: list[str] | None,
) -> list[tuple]:
    """Load raw summary rows for revalidation-aware listing."""
    if summary_ids:
        return db.execute(
            _build_in_clause_query(
                "SELECT id, main_point, based_on, trust_level, summary_scope, "
                "trust_level_score, source_record_ids, record_relations, "
                "created_at "
                "FROM summaries WHERE id IN",
                len(summary_ids),
            ),
            tuple(summary_ids),
        ).fetchall()
    return db.execute(
        "SELECT id, main_point, based_on, trust_level, summary_scope, "
        "trust_level_score, source_record_ids, record_relations, "
        "created_at "
        "FROM summaries",
    ).fetchall()


def _build_revalidated_distillation_record(
    db: DatabaseManager,
    row: tuple,
) -> dict[str, object]:
    """Build one revalidated summary record from a stored DB row."""
    source_record_ids = _normalize_source_record_ids(row[6])
    record_relations = _normalize_record_relations(row[7], source_record_ids)
    relation_metrics = _build_distillation_relation_metrics(
        db,
        source_record_ids=source_record_ids,
        record_relations=record_relations,
        created_at=str(row[8] or ""),
    )
    return {
        "id": row[0],
        "main_point": row[1],
        "based_on": json.loads(row[2]) if row[2] else [],
        "trust_level": row[3],
        "scope": _normalize_summary_scope(str(row[4] or "long_term")),
        "trust_level_score": row[5],
        "effective_trust_level_score": max(
            1,
            int(row[5]) - relation_metrics["total_penalty"],
        ),
        "revalidation_status": _revalidation_status(
            age_penalty=int(relation_metrics["age_penalty"]),
            relation_status=str(relation_metrics["relation_status"]),
        ),
        "age_penalty": relation_metrics["age_penalty"],
        "active_support_count": relation_metrics["active_support_count"],
        "active_conflict_count": relation_metrics["active_conflict_count"],
        "active_outdate_count": relation_metrics["active_outdate_count"],
        "supporting_record_count": len(source_record_ids),
        "source_record_ids": source_record_ids,
        "record_relations": record_relations,
        "created_at": row[8],
    }


def _build_distillation_relation_metrics(
    db: DatabaseManager,
    *,
    source_record_ids: list[str],
    record_relations: list[dict[str, str]],
    created_at: str,
) -> dict[str, object]:
    """Compute the relation and aging metrics for one summary row."""
    record_state_map = _build_record_state_map(db, source_record_ids)
    supporting_record_ids = _relation_record_ids(record_relations, SUPPORTS_RELATION)
    conflicting_record_ids = _relation_record_ids(record_relations, CONFLICTS_RELATION)
    outdating_record_ids = _relation_record_ids(record_relations, OUTDATES_RELATION)
    age_penalty = _compute_age_penalty(created_at)
    support_penalty, active_support_count = _compute_support_penalty(
        supporting_record_ids,
        record_state_map,
    )
    active_conflict_count = _active_relation_count(
        conflicting_record_ids,
        record_state_map,
    )
    active_outdate_count = _active_relation_count(
        outdating_record_ids,
        record_state_map,
    )
    relation_penalty, relation_status = _relation_penalty_and_status(
        active_support_count=active_support_count,
        supporting_record_count=len(supporting_record_ids),
        active_conflict_count=active_conflict_count,
        active_outdate_count=active_outdate_count,
    )
    return {
        "age_penalty": age_penalty,
        "active_support_count": active_support_count,
        "active_conflict_count": active_conflict_count,
        "active_outdate_count": active_outdate_count,
        "relation_status": relation_status,
        "total_penalty": age_penalty + support_penalty + relation_penalty,
    }


def _sort_revalidated_distillation_records(
    summary_records: list[dict[str, object]],
) -> None:
    """Sort revalidated summaries by trust, support, and recency."""
    summary_records.sort(
        key=lambda record: (
            int(record["effective_trust_level_score"]),
            int(record["active_support_count"]),
            str(record["created_at"]),
        ),
        reverse=True,
    )


def list_revalidated_distillations(
    db: DatabaseManager,
    limit: int = 100,
    offset: int = 0,
    summary_ids: list[str] | None = None,
) -> list[dict[str, object]]:
    """List summaries with age/support-aware effective trust metadata."""
    rows = _load_distillation_rows(db, summary_ids)
    summary_records = [_build_revalidated_distillation_record(db, row) for row in rows]
    _sort_revalidated_distillation_records(summary_records)
    return summary_records[offset : offset + limit]


def list_summaries(
    db: DatabaseManager,
    limit: int = 100,
    offset: int = 0,
) -> list[tuple]:
    """List summaries ordered by creation date.

    Args:
        db: Open DatabaseManager instance.
        limit: Maximum number of summaries.
        offset: Number of summaries to skip.

    Returns:
        List of (id, main_point, trust_level, created_at) tuples.
    """
    summary_records = list_revalidated_distillations(
        db,
        limit=limit,
        offset=offset,
    )
    return [
        (
            record["id"],
            record["main_point"],
            record["trust_level"],
            record["created_at"],
        )
        for record in summary_records
    ]


def get_distillation(
    db: DatabaseManager,
    summary_id: str,
) -> tuple | None:
    """Get a single summary by ID.

    Args:
        db: Open DatabaseManager instance.
        summary_id: ID of the summary.

    Returns:
        (id, main_point, based_on, trust_level, source_record_ids,
         created_at) tuple, or None if not found.
    """
    cursor = db.execute(
        "SELECT id, main_point, based_on, trust_level, summary_scope, "
        "source_record_ids, record_relations, created_at "
        "FROM summaries WHERE id = ?",
        (summary_id,),
    )
    return cursor.fetchone()


def delete_distillation(
    db: DatabaseManager,
    summary_id: str,
) -> bool:
    """Delete a summary by ID.

    Args:
        db: Open DatabaseManager instance.
        summary_id: ID of the summary to delete.

    Returns:
        True if a summary was deleted, False if not found.
    """
    row = db.execute(
        "SELECT rowid FROM summaries WHERE id = ?",
        (summary_id,),
    ).fetchone()
    if row is None:
        return False

    cursor = db.execute(
        "DELETE FROM summaries WHERE id = ?",
        (summary_id,),
    )
    delete_summary_index(db, row[0])
    db.commit()
    deleted = cursor.rowcount > 0
    if deleted:
        logger.info("Summary %s deleted", summary_id)
    return deleted


def append_summary_sources(
    db: DatabaseManager,
    summary_id: str,
    source_record_ids: list[str],
    record_relations: list[dict[str, str]],
    *,
    main_point: str = "",
    based_on: list[str] | None = None,
    trust_level: str = "",
    summary_scope: str = "",
    trust_level_score: int | None = None,
) -> bool:
    """Attach new linked records to an existing summary.

    Returns ``False`` when the summary no longer exists.
    """
    row = db.execute(
        "SELECT main_point, based_on, trust_level, summary_scope, "
        "trust_level_score, source_record_ids, record_relations "
        "FROM summaries WHERE id = ?",
        (summary_id,),
    ).fetchone()
    if row is None:
        return False

    existing_summary = _build_existing_summary_source_state(row)
    merged_summary = _merge_summary_source_state(
        existing_summary=existing_summary,
        source_record_ids=source_record_ids,
        record_relations=record_relations,
        main_point=main_point,
        based_on=based_on,
        trust_level=trust_level,
        summary_scope=summary_scope,
        trust_level_score=trust_level_score,
    )
    _update_summary_sources(
        db,
        summary_id=summary_id,
        merged_summary=merged_summary,
    )
    db.commit()
    return True


def _build_existing_summary_source_state(row: tuple) -> dict[str, object]:
    """Build normalized existing summary source state from one DB row."""
    existing_source_ids = _normalize_source_record_ids(row[5])
    return {
        "main_point": str(row[0] or ""),
        "based_on": json.loads(row[1]) if row[1] else [],
        "trust_level": str(row[2] or "medium"),
        "scope": _normalize_summary_scope(str(row[3] or "long_term")),
        "trust_level_score": int(row[4] or 2),
        "source_record_ids": existing_source_ids,
        "record_relations": _normalize_record_relations(row[6], existing_source_ids),
    }


def _merge_summary_source_state(
    *,
    existing_summary: dict[str, object],
    source_record_ids: list[str],
    record_relations: list[dict[str, str]],
    main_point: str,
    based_on: list[str] | None,
    trust_level: str,
    summary_scope: str,
    trust_level_score: int | None,
) -> dict[str, object]:
    """Merge new source links and fields into existing summary state."""
    merged_source_ids = _merge_source_record_ids(
        list(existing_summary["source_record_ids"]),
        source_record_ids,
    )
    merged_relations = _merge_record_relations(
        list(existing_summary["record_relations"]),
        record_relations,
    )
    merged_main_point = _choose_richer_main_point(
        str(existing_summary["main_point"]),
        main_point,
    )
    merged_based_on = _merge_based_on_reasons(
        list(existing_summary["based_on"]),
        based_on or [],
    )
    merged_trust_score = max(
        int(existing_summary["trust_level_score"]),
        trust_level_score if trust_level_score is not None else 0,
    )
    merged_trust_label, merged_trust_score = _normalize_trust_level_label(
        trust_level or str(existing_summary["trust_level"]),
        merged_trust_score or int(existing_summary["trust_level_score"]),
    )
    return {
        "main_point": merged_main_point,
        "based_on": merged_based_on,
        "trust_level": merged_trust_label,
        "summary_scope": _merge_summary_scope(
            str(existing_summary["scope"]),
            summary_scope,
        ),
        "trust_level_score": merged_trust_score,
        "source_record_ids": merged_source_ids,
        "record_relations": merged_relations,
    }


def _update_summary_sources(
    db: DatabaseManager,
    *,
    summary_id: str,
    merged_summary: dict[str, object],
) -> None:
    """Persist merged source data back into the summaries table."""
    db.execute(
        "UPDATE summaries SET main_point = ?, based_on = ?, trust_level = ?, "
        "summary_scope = ?, trust_level_score = ?, source_record_ids = ?, "
        "record_relations = ? "
        "WHERE id = ?",
        (
            merged_summary["main_point"],
            json.dumps(merged_summary["based_on"]),
            merged_summary["trust_level"],
            merged_summary["summary_scope"],
            merged_summary["trust_level_score"],
            json.dumps(merged_summary["source_record_ids"]),
            json.dumps(merged_summary["record_relations"]),
            summary_id,
        ),
    )


def count_distillations(db: DatabaseManager) -> int:
    """Count total summaries in the database.

    Args:
        db: Open DatabaseManager instance.

    Returns:
        Total number of summaries.
    """
    cursor = db.execute("SELECT count(*) FROM summaries")
    return cursor.fetchone()[0]
