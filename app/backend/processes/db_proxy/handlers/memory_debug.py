"""Admin read-only reports over the graph-era memory tiers.

Provides make_get_memory_context_view_handler,
make_get_memory_quality_report_handler, and
make_get_memory_timeline_handler. Tiers: messages (raw), records
(observations), assertions (graph knowledge).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..common import OperationHandler, validate_params
from ..params import GetMemoryTimelineParams


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager

_TIMELINE_TYPES = ("message", "record", "assertion")


def _list_messages_rows(db: DatabaseManager, limit: int) -> list[dict[str, Any]]:
    """Load newest messages for reports."""
    rows = db.execute(
        "SELECT id, role, content, conversation_id, created_at FROM messages "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "role": r[1],
            "content": r[2],
            "conversation_id": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


def _list_records_rows(db: DatabaseManager, limit: int) -> list[dict[str, Any]]:
    """Load newest records for reports."""
    rows = db.execute(
        "SELECT id, summary, source_message_ids, tainted, created_at "
        "FROM records ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "summary": r[1],
            "source_message_ids": r[2],
            "tainted": bool(r[3]),
            "created_at": r[4],
        }
        for r in rows
    ]


def _list_assertion_rows(db: DatabaseManager, limit: int) -> list[dict[str, Any]]:
    """Load newest assertions for reports."""
    rows = db.execute(
        "SELECT id, relation, assertion_text, trust_level, valid_from, "
        "invalid_from, source_record_ids, created_at "
        "FROM assertions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "relation": r[1],
            "assertion_text": r[2],
            "trust_level": r[3],
            "valid_from": r[4],
            "invalid_from": r[5],
            "source_record_ids": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]


def _memory_object_counts(db: DatabaseManager) -> dict[str, int]:
    """Count rows per memory tier."""
    return {
        "message": db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "record": db.execute("SELECT COUNT(*) FROM records").fetchone()[0],
        "assertion": db.execute("SELECT COUNT(*) FROM assertions").fetchone()[0],
    }


def make_get_memory_context_view_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for the memory context snapshot report."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        del params
        return {
            "tiers": {
                "recent_messages": {
                    "entries": _list_messages_rows(db, limit=50),
                },
                "records": {"entries": _list_records_rows(db, limit=20)},
                "assertions": {
                    "entries": _list_assertion_rows(db, limit=20),
                },
            }
        }

    return handle


def _normalized_text_key(text: str) -> str:
    """Normalize assertion text for duplicate detection."""
    return " ".join(text.split()).casefold()


def make_get_memory_quality_report_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for the graph-era memory quality report."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        del params
        counts = _memory_object_counts(db)
        assertions = _list_assertion_rows(
            db,
            limit=max(counts["assertion"], 1),
        )
        known_record_ids = {
            row[0] for row in db.execute("SELECT id FROM records").fetchall()
        }
        valid = [a for a in assertions if a["invalid_from"] is None]
        invalidated_count = len(assertions) - len(valid)
        missing_evidence = 0
        without_evidence = 0
        for assertion in valid:
            try:
                cited = json.loads(assertion["source_record_ids"])
            except (json.JSONDecodeError, TypeError):
                cited = []
            if not cited:
                without_evidence += 1
            elif any(str(rid) not in known_record_ids for rid in cited):
                missing_evidence += 1
        text_keys = [_normalized_text_key(a["assertion_text"]) for a in valid]
        duplicate_texts = len(text_keys) - len(set(text_keys))
        orphan_entities = db.execute(
            "SELECT COUNT(*) FROM entities e WHERE NOT EXISTS ("
            "SELECT 1 FROM assertions a WHERE a.subject_entity_id = e.id "
            "OR a.object_entity_id = e.id)",
        ).fetchone()[0]
        total_valid = max(len(valid), 1)
        quality_score = max(
            0.0,
            1.0 - (missing_evidence + without_evidence + duplicate_texts) / total_valid,
        )
        return {
            "counts": counts,
            "assertions_valid": len(valid),
            "assertions_invalidated": invalidated_count,
            "assertions_without_evidence": without_evidence,
            "assertions_with_missing_evidence": missing_evidence,
            "duplicate_assertion_texts": duplicate_texts,
            "orphan_entities": orphan_entities,
            "quality_score": round(quality_score, 3),
        }

    return handle


def _timeline_entry_sql(entry_type: str) -> str:
    """Return the per-tier SELECT for the timeline union."""
    if entry_type == "message":
        return (
            "SELECT 'message' AS entry_type, id, content AS text, created_at "
            "FROM messages"
        )
    if entry_type == "record":
        return (
            "SELECT 'record' AS entry_type, id, summary AS text, created_at "
            "FROM records"
        )
    return (
        "SELECT 'assertion' AS entry_type, id, assertion_text AS text, "
        "created_at FROM assertions"
    )


def make_get_memory_timeline_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for the merged memory timeline report."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        payload = validate_params(params, GetMemoryTimelineParams)
        requested_type = payload.entry_type
        types = (
            [requested_type]
            if requested_type in _TIMELINE_TYPES
            else list(_TIMELINE_TYPES)
        )
        union_sql = " UNION ALL ".join(
            _timeline_entry_sql(entry_type) for entry_type in types
        )
        order = "ASC" if payload.sort == "asc" else "DESC"
        rows = db.execute(
            f"SELECT * FROM ({union_sql}) "  # noqa: S608 — fixed fragments
            f"ORDER BY created_at {order} LIMIT ? OFFSET ?",
            (payload.limit, payload.offset),
        ).fetchall()
        counts = _memory_object_counts(db)
        total_count = sum(counts[entry_type] for entry_type in types)
        return {
            "entries": [
                {
                    "type": r[0],
                    "id": r[1],
                    "text": r[2],
                    "created_at": r[3],
                }
                for r in rows
            ],
            "total_count": total_count,
        }

    return handle
