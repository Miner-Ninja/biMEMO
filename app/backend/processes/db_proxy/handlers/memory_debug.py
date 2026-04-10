"""DB proxy handlers for admin memory debug reports."""

from __future__ import annotations

import contextlib
import json
import re
from typing import TYPE_CHECKING, Any

from ..common import OperationHandler, parse_content, validate_params
from ..params import GetMemoryTimelineParams


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


def _normalize_string_list(raw_items: object) -> list[str]:
    """Normalize a structured field into a clean list of strings."""
    if isinstance(raw_items, list):
        return [str(item).strip() for item in raw_items if str(item).strip()]
    if isinstance(raw_items, str):
        stripped = raw_items.strip()
        return [stripped] if stripped else []
    return []


def _quality_ratio(matched_count: int, total_count: int) -> float:
    """Return a safe quality ratio in the 0..1 range."""
    if total_count <= 0:
        return 1.0
    return matched_count / total_count


def _weighted_status_score(status_counts: dict[str, int], total_count: int) -> float:
    """Convert summary status distribution into a normalized quality score."""
    if total_count <= 0:
        return 1.0
    weighted_total = (
        status_counts.get("fresh", 0) * 1.0
        + status_counts.get("outdated", 0) * 0.85
        + status_counts.get("partial_support", 0) * 0.6
        + status_counts.get("mixed_support", 0) * 0.45
        + status_counts.get("conflicted", 0) * 0.2
        + status_counts.get("stale", 0) * 0.3
        + status_counts.get("orphaned", 0) * 0.0
    )
    return weighted_total / total_count


def _memory_list_value(row: tuple[Any, ...], index: int) -> list[Any]:
    """Decode one stored JSON list column from a diagnostic row."""
    value = row[index]
    if not value:
        return []
    return json.loads(value)


def _json_object_update(raw_value: object) -> dict[str, Any]:
    """Decode one optional JSON object for debug metadata updates."""
    if not raw_value:
        return {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        parsed = json.loads(raw_value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _normalize_main_point_key(main_point: str) -> str:
    """Normalize a summary main point for duplicate detection."""
    lowered = main_point.lower()
    alphanumeric_only = re.sub(r"[^\w\s]+", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", alphanumeric_only, flags=re.UNICODE).strip()


def _duplicate_main_point_count(summaries: list[dict[str, Any]]) -> int:
    """Count duplicate summary main points after light normalization."""
    normalized_main_points = [
        _normalize_main_point_key(str(summary.get("main_point", "")))
        for summary in summaries
        if str(summary.get("main_point", "")).strip()
    ]
    unique_main_points = {
        main_point for main_point in normalized_main_points if main_point
    }
    return max(0, len(normalized_main_points) - len(unique_main_points))


def _collect_record_quality_stats(
    records: list[dict[str, Any]],
    message_ids: set[str],
) -> dict[str, int]:
    """Collect quality counters for record entries."""
    stats = {
        "with_provenance": 0,
        "with_key_facts": 0,
        "with_action_items": 0,
        "orphaned": 0,
    }
    for record in records:
        source_message_ids = _normalize_string_list(
            record.get("source_message_ids", [])
        )
        key_facts = _normalize_string_list(record.get("key_facts", []))
        action_items = _normalize_string_list(record.get("action_items", []))
        if source_message_ids:
            stats["with_provenance"] += 1
        if key_facts:
            stats["with_key_facts"] += 1
        if action_items:
            stats["with_action_items"] += 1
        if source_message_ids and any(
            message_identifier not in message_ids
            for message_identifier in source_message_ids
        ):
            stats["orphaned"] += 1
    return stats


def _empty_summary_status_counts() -> dict[str, int]:
    """Return the fixed summary-status counter map."""
    return {
        "fresh": 0,
        "outdated": 0,
        "partial_support": 0,
        "mixed_support": 0,
        "conflicted": 0,
        "stale": 0,
        "orphaned": 0,
    }


def _collect_summary_quality_stats(
    summaries: list[dict[str, Any]],
    record_ids: set[str],
) -> dict[str, Any]:
    """Collect quality counters for summary entries."""
    status_counts = _empty_summary_status_counts()
    stats: dict[str, Any] = {
        "with_records": 0,
        "with_live_support": 0,
        "with_based_on": 0,
        "orphaned": 0,
        "status_counts": status_counts,
    }
    for summary in summaries:
        supporting_record_ids = _normalize_string_list(
            summary.get("record_ids", summary.get("source_record_ids", []))
        )
        based_on_items = _normalize_string_list(summary.get("based_on", []))
        summary_status = str(summary.get("revalidation_status", "fresh"))
        has_live_support = bool(supporting_record_ids) and all(
            record_identifier in record_ids
            for record_identifier in supporting_record_ids
        )

        if supporting_record_ids:
            stats["with_records"] += 1
        if has_live_support:
            stats["with_live_support"] += 1
        if based_on_items:
            stats["with_based_on"] += 1
        if supporting_record_ids and any(
            record_identifier not in record_ids
            for record_identifier in supporting_record_ids
        ):
            stats["orphaned"] += 1
        if summary_status in status_counts:
            status_counts[summary_status] += 1
    return stats


def _memory_quality_score(
    record_total: int,
    summary_total: int,
    record_stats: dict[str, int],
    summary_stats: dict[str, Any],
    duplicate_summaries: int,
) -> int:
    """Compute the overall memory quality score."""
    duplicate_health = (
        1.0
        if summary_total == 0
        else max(0.0, 1 - (duplicate_summaries / summary_total))
    )
    return round(
        100
        * (
            _quality_ratio(record_stats["with_provenance"], record_total) * 0.25
            + (
                (
                    _quality_ratio(record_stats["with_key_facts"], record_total)
                    + _quality_ratio(record_stats["with_action_items"], record_total)
                )
                / 2
            )
            * 0.15
            + (1.0 - _quality_ratio(record_stats["orphaned"], record_total)) * 0.10
            + _quality_ratio(summary_stats["with_records"], summary_total) * 0.15
            + _quality_ratio(summary_stats["with_based_on"], summary_total) * 0.10
            + _weighted_status_score(
                summary_stats["status_counts"],
                summary_total,
            )
            * 0.15
            + (1.0 - _quality_ratio(summary_stats["orphaned"], summary_total)) * 0.05
            + duplicate_health * 0.05
        )
    )


def _memory_quality_issues(
    record_total: int,
    summary_total: int,
    record_stats: dict[str, int],
    summary_stats: dict[str, Any],
    duplicate_summaries: int,
) -> list[str]:
    """Build human-readable quality issues for the current memory state."""
    del summary_total
    issues: list[str] = []
    missing_record_provenance = record_total - record_stats["with_provenance"]
    if missing_record_provenance > 0:
        issues.append(
            f"{missing_record_provenance} records are missing source "
            "message provenance."
        )
    if record_stats["orphaned"] > 0:
        issues.append(
            f"{record_stats['orphaned']} records reference message IDs "
            "that are no longer available."
        )
    if summary_stats["status_counts"]["orphaned"] > 0:
        issues.append(
            f"{summary_stats['status_counts']['orphaned']} summaries have lost "
            "all linked records."
        )
    if summary_stats["status_counts"]["stale"] > 0:
        issues.append(
            f"{summary_stats['status_counts']['stale']} summaries are stale "
            "and should be revalidated."
        )
    if duplicate_summaries > 0:
        issues.append(
            f"{duplicate_summaries} summaries appear to duplicate "
            "an existing main point."
        )
    if record_total and record_stats["with_action_items"] == 0:
        issues.append(
            "Records are not producing any action items, which "
            "suggests shallow compression."
        )
    return issues


def _build_quality_report(
    messages: list[dict[str, Any]],
    records: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a diagnostic quality report for the current memory state."""
    message_ids = {str(message.get("id", "")) for message in messages}
    record_ids = {str(record.get("id", "")) for record in records}
    record_stats = _collect_record_quality_stats(records, message_ids)
    summary_stats = _collect_summary_quality_stats(summaries, record_ids)
    duplicate_summaries = _duplicate_main_point_count(summaries)
    record_total = len(records)
    summary_total = len(summaries)
    quality_score = _memory_quality_score(
        record_total,
        summary_total,
        record_stats,
        summary_stats,
        duplicate_summaries,
    )
    issues = _memory_quality_issues(
        record_total,
        summary_total,
        record_stats,
        summary_stats,
        duplicate_summaries,
    )

    return {
        "quality_score": quality_score,
        "issues": issues,
        "messages": {"total": len(messages)},
        "records": {
            "total": record_total,
            "with_provenance": record_stats["with_provenance"],
            "provenance_coverage": round(
                _quality_ratio(record_stats["with_provenance"], record_total),
                3,
            ),
            "with_key_facts": record_stats["with_key_facts"],
            "key_fact_coverage": round(
                _quality_ratio(record_stats["with_key_facts"], record_total),
                3,
            ),
            "with_action_items": record_stats["with_action_items"],
            "action_item_coverage": round(
                _quality_ratio(record_stats["with_action_items"], record_total),
                3,
            ),
            "orphaned": record_stats["orphaned"],
        },
        "summaries": {
            "total": summary_total,
            "with_support_links": summary_stats["with_records"],
            "support_link_coverage": round(
                _quality_ratio(summary_stats["with_records"], summary_total),
                3,
            ),
            "with_live_support": summary_stats["with_live_support"],
            "live_support_coverage": round(
                _quality_ratio(summary_stats["with_live_support"], summary_total),
                3,
            ),
            "with_based_on": summary_stats["with_based_on"],
            "based_on_coverage": round(
                _quality_ratio(summary_stats["with_based_on"], summary_total),
                3,
            ),
            "status_breakdown": summary_stats["status_counts"],
            "orphaned_links": summary_stats["orphaned"],
            "duplicate_main_points": duplicate_summaries,
        },
    }


def _memory_record_entry(row: tuple[Any, ...]) -> dict[str, Any]:
    """Build one memory-record debug entry from a DB row."""
    return {
        "id": row[0],
        "summary": row[1],
        "key_facts": _memory_list_value(row, 2),
        "action_items": _memory_list_value(row, 3),
        "conversation_id": row[4],
        "created_at": row[5],
        "tainted": bool(row[6]),
        "source_message_ids": _memory_list_value(row, 7),
        "memory_kinds": _memory_list_value(row, 8),
    }


def _memory_summary_entry(row: tuple[Any, ...]) -> dict[str, Any]:
    """Build one memory-summary debug entry from a DB row."""
    return {
        "id": row[0],
        "main_point": row[1],
        "based_on": _memory_list_value(row, 2),
        "trust_level": row[3],
        "scope": row[4],
        "created_at": row[5],
        "record_ids": _memory_list_value(row, 6),
        "record_relations": _memory_list_value(row, 7),
        "revalidation_status": row[8],
        "effective_trust_level_score": row[9],
        "active_support_count": row[10],
        "supporting_record_count": row[11],
    }


def _memory_entry_meta(
    *,
    role: str = "",
    key_facts: list[Any] | None = None,
    based_on: list[Any] | None = None,
) -> dict[str, Any]:
    """Build one compact meta payload for memory debug entries."""
    meta: dict[str, Any] = {}
    if role:
        meta["role"] = role
    if key_facts is not None:
        meta["key_facts"] = key_facts
    if based_on is not None:
        meta["based_on"] = based_on
    return meta


def _message_entry(message: dict[str, Any]) -> dict[str, Any]:
    meta = _memory_entry_meta(role=str(message.get("role", "")))
    raw_meta = message.get("meta")
    if raw_meta:
        meta.update(_json_object_update(raw_meta))
    return {
        "type": "message",
        "id": message["id"],
        "content": message.get("content", ""),
        "created_at": message.get("created_at", ""),
        "meta": meta,
    }


def _record_entry(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "record",
        "id": record["id"],
        "content": record.get("summary", ""),
        "created_at": record.get("created_at", ""),
        "meta": _memory_entry_meta(key_facts=record.get("key_facts", [])),
    }


def _summary_entry(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "summary",
        "id": summary["id"],
        "content": summary.get("main_point", ""),
        "created_at": summary.get("created_at", ""),
        "meta": _memory_entry_meta(based_on=summary.get("based_on", [])),
    }


def _decoded_json_list(value: str | None) -> list[Any]:
    """Decode one stored JSON list field used by memory debug views."""
    return json.loads(value) if value else []


def _message_row_entry(row: tuple[Any, ...]) -> dict[str, Any]:
    """Build one raw message entry from the DB row."""
    return {
        "id": row[0],
        "role": row[1],
        "content": parse_content(row[2]),
        "created_at": row[3],
        "meta": row[4],
        "conversation_id": row[5],
    }


def _record_row_entry(row: tuple[Any, ...]) -> dict[str, Any]:
    """Build one raw record entry from the DB row."""
    return {
        "id": row[0],
        "summary": row[1],
        "key_facts": _decoded_json_list(row[2]),
        "action_items": _decoded_json_list(row[3]),
        "conversation_id": row[4],
        "created_at": row[5],
        "session_id": row[6],
        "source_message_ids": _decoded_json_list(row[7]),
        "memory_kinds": _decoded_json_list(row[8]),
        "tainted": bool(row[9]),
    }


def _summary_row_entry(row: tuple[Any, ...]) -> dict[str, Any]:
    """Build one raw summary entry from the DB row."""
    return {
        "id": row[0],
        "main_point": row[1],
        "based_on": _decoded_json_list(row[2]),
        "trust_level": row[3],
        "source_record_ids": _decoded_json_list(row[4]),
        "record_relations": _decoded_json_list(row[5]),
        "scope": row[6],
        "created_at": row[7],
        "updated_at": row[7],
        "revalidation_status": "fresh",
        "revalidation_details": "",
    }


def _list_messages_rows(
    db: DatabaseManager,
    limit: int,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Load raw messages in the same shape used by admin routes."""
    rows = db.execute(
        """
        SELECT id, role, content, created_at, meta, conversation_id
        FROM messages
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [_message_row_entry(row) for row in rows]


def _list_records_rows(
    db: DatabaseManager,
    limit: int,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Load raw records in the same shape used by admin routes."""
    rows = db.execute(
        """
        SELECT id, summary, key_facts, action_items, conversation_id, created_at,
               session_id, source_message_ids, memory_kinds, tainted
        FROM records
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [_record_row_entry(row) for row in rows]


def _list_summaries_rows(
    db: DatabaseManager,
    limit: int,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Load raw summaries in the same shape used by admin routes."""
    rows = db.execute(
        """
        SELECT id, main_point, based_on, trust_level, source_record_ids,
               record_relations, summary_scope, created_at
        FROM summaries
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [_summary_row_entry(row) for row in rows]


def _memory_snapshot_tiers(
    messages: list[dict[str, Any]],
    records: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build tiered memory snapshot payload for the context-view report."""
    return {
        "recent_messages": {
            "count": len(messages),
            "entries": messages,
        },
        "records": {
            "count": len(records),
            "entries": records,
        },
        "summaries": {
            "count": len(summaries),
            "entries": summaries,
        },
    }


def _memory_object_counts(db: DatabaseManager) -> dict[str, int]:
    """Load top-level counts for messages, records, and summaries."""
    return {
        "message": int(db.execute("SELECT count(*) FROM messages").fetchone()[0]),
        "record": int(db.execute("SELECT count(*) FROM records").fetchone()[0]),
        "summary": int(db.execute("SELECT count(*) FROM summaries").fetchone()[0]),
    }


def _timeline_fetch_window(
    payload: GetMemoryTimelineParams,
) -> tuple[int, int]:
    """Resolve fetch limit and offset for one timeline query."""
    if payload.entry_type:
        return payload.limit, payload.offset
    return payload.limit + payload.offset, 0


def _extend_timeline_entries(
    entries: list[dict[str, Any]],
    db: DatabaseManager,
    entry_type: str,
    fetch_limit: int,
    fetch_offset: int,
) -> None:
    """Append one requested entry type to the timeline buffer."""
    if entry_type == "message":
        entries.extend(
            _message_entry(message)
            for message in _list_messages_rows(
                db,
                limit=fetch_limit,
                offset=fetch_offset,
            )
        )
        return
    if entry_type == "record":
        entries.extend(
            _record_entry(record)
            for record in _list_records_rows(
                db,
                limit=fetch_limit,
                offset=fetch_offset,
            )
        )
        return
    entries.extend(
        _summary_entry(summary)
        for summary in _list_summaries_rows(
            db,
            limit=fetch_limit,
            offset=fetch_offset,
        )
    )


def _timeline_entries(
    db: DatabaseManager,
    payload: GetMemoryTimelineParams,
    fetch_limit: int,
    fetch_offset: int,
) -> list[dict[str, Any]]:
    """Load and sort timeline entries for the requested type filter."""
    entries: list[dict[str, Any]] = []
    requested_types = (
        [payload.entry_type] if payload.entry_type else ["message", "record", "summary"]
    )
    for requested_type in requested_types:
        _extend_timeline_entries(
            entries,
            db,
            requested_type,
            fetch_limit,
            fetch_offset,
        )
    entries.sort(
        key=lambda entry: entry.get("created_at", ""),
        reverse=(payload.sort != "asc"),
    )
    return entries


def _slice_timeline_entries(
    entries: list[dict[str, Any]],
    payload: GetMemoryTimelineParams,
) -> list[dict[str, Any]]:
    """Apply final paging to already loaded timeline entries."""
    if payload.entry_type:
        return entries[: payload.limit]
    return entries[payload.offset : payload.offset + payload.limit]


def _timeline_total_count(
    counts: dict[str, int],
    payload: GetMemoryTimelineParams,
    entries: list[dict[str, Any]],
) -> int:
    """Resolve the total_count field for timeline output."""
    if not payload.entry_type:
        return counts["message"] + counts["record"] + counts["summary"]
    return counts.get(payload.entry_type, len(entries))


def make_get_memory_context_view_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for the memory context snapshot report."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        del params
        messages = _list_messages_rows(db, limit=50)
        records = _list_records_rows(db, limit=20)
        summaries = _list_summaries_rows(db, limit=10)
        return {"tiers": _memory_snapshot_tiers(messages, records, summaries)}

    return handle


def make_get_memory_quality_report_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for the memory quality report."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        del params
        counts = _memory_object_counts(db)
        messages = _list_messages_rows(db, limit=max(counts["message"], 1))
        records = _list_records_rows(db, limit=max(counts["record"], 1))
        summaries = _list_summaries_rows(db, limit=max(counts["summary"], 1))
        return _build_quality_report(
            messages=messages,
            records=records,
            summaries=summaries,
        )

    return handle


def make_get_memory_timeline_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for the memory timeline report."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        payload = validate_params(params, GetMemoryTimelineParams)
        counts = _memory_object_counts(db)
        fetch_limit, fetch_offset = _timeline_fetch_window(payload)
        entries = _timeline_entries(
            db,
            payload,
            fetch_limit,
            fetch_offset,
        )
        entries = _slice_timeline_entries(entries, payload)
        total_count = _timeline_total_count(counts, payload, entries)
        return {
            "entries": entries,
            "total_count": total_count,
        }

    return handle
