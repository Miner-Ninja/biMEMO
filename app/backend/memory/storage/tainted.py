"""Helpers for tainted record filtering."""

import json
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

_RECORD_FIELDS_SQL = (
    "SELECT id, summary, key_facts, action_items, memory_kinds FROM records "
)


def _load_reflected_record_ids(db: DatabaseManager) -> set[str]:
    """Collect record IDs already covered by summaries."""
    summary_rows = db.execute("SELECT source_record_ids FROM summaries").fetchall()
    reflected_record_ids: set[str] = set()
    for (source_ids_json,) in summary_rows:
        reflected_record_ids.update(_summary_source_ids(source_ids_json))
    return reflected_record_ids


def _summary_source_ids(source_ids_json: object) -> set[str]:
    """Return valid source record IDs from one summary JSON cell."""
    if not source_ids_json:
        return set()
    try:
        source_ids = json.loads(source_ids_json)
    except json.JSONDecodeError:
        logger.warning("Invalid source_record_ids JSON in summaries table")
        return set()
    if not isinstance(source_ids, list):
        return set()
    return {source_id for source_id in source_ids if isinstance(source_id, str)}


def _record_row_query(
    session_id: str | None,
    limit: int,
) -> tuple[str, tuple[object, ...]]:
    """Build the untainted-record query and its parameters."""
    if session_id is not None:
        return (
            _RECORD_FIELDS_SQL
            + "WHERE tainted = 0 "
            + "OR session_id = ? "
            + "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
    return (
        _RECORD_FIELDS_SQL + "WHERE tainted = 0 " + "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


def fetch_untainted_records(
    db: DatabaseManager,
    session_id: str | None = None,
    limit: int = 50,
) -> list[tuple]:
    """Fetch records excluding tainted rows from other sessions."""
    query, params = _record_row_query(session_id, limit)
    cursor = db.execute(query, params)
    return cursor.fetchall()


def fetch_untainted_for_distiller(
    db: DatabaseManager,
    limit: int = 50,
) -> list[tuple]:
    """Fetch only unreflected, untainted records for distiller input."""
    record_rows = db.execute(
        "SELECT id, summary, key_facts, action_items, memory_kinds, source_message_ids "
        "FROM records "
        "WHERE tainted = 0 "
        "ORDER BY created_at DESC",
    ).fetchall()
    reflected_record_ids = _load_reflected_record_ids(db)

    unreflected_rows = [
        row for row in record_rows if row[0] not in reflected_record_ids
    ]
    return unreflected_rows[:limit]
