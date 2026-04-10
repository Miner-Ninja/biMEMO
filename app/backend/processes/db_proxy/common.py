"""Shared constants and helper functions for DB proxy handlers."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from backend.security.database import DatabaseManager


# --- Hash anchor interval ---
HASH_ANCHOR_INTERVAL = 100
AUDIT_INITIALIZATION_VECTOR = "bimemo-audit-chain-v1"
JWT_CONTEXT_LABEL = b"bimemo-jwt-signing-v1"
LLM_SERVICE_TYPES = ("openrouter", "ollama", "custom")
OperationHandler = Callable[[dict[str, Any] | None], Awaitable[dict[str, Any]]]


def _audit_entry_fields(
    previous_hash: str,
    timestamp: float,
    event_type: str,
    tool_name: str | None,
    parameters: str | None,
    result: str | None,
    zone: str | None,
    duration_ms: float | None,
) -> list[object]:
    """Return the ordered fields used to hash one audit entry."""
    return [
        previous_hash,
        timestamp,
        event_type,
        tool_name,
        parameters,
        result,
        zone,
        duration_ms,
    ]


def validate_params(
    params: dict[str, Any] | None,
    model_class: type[BaseModel],
) -> BaseModel:
    """Validate operation parameters against a Pydantic model."""
    if params is None:
        params = {}
    return model_class.model_validate(params)


def compute_audit_hash(
    previous_hash: str,
    timestamp: float,
    event_type: str,
    tool_name: str | None,
    parameters: str | None,
    result: str | None,
    zone: str | None,
    duration_ms: float | None,
) -> str:
    """Compute a SHA-256 hash for an audit log entry."""
    data = json.dumps(
        _audit_entry_fields(
            previous_hash,
            timestamp,
            event_type,
            tool_name,
            parameters,
            result,
            zone,
            duration_ms,
        ),
        sort_keys=True,
    )
    return hashlib.sha256(data.encode()).hexdigest()


def build_in_clause_query(base_sql: str, item_count: int) -> str:
    """Build a SQL query with a validated ``IN (?, ?, ...)`` clause."""
    placeholders = ", ".join("?" for _ in range(item_count))
    return f"{base_sql} ({placeholders})"


def count_rows_for_table(
    db: DatabaseManager,
    table_name: str,
    query_map: dict[str, str],
) -> int:
    """Count rows for a known table using a fixed SQL mapping."""
    row = db.execute(query_map[table_name]).fetchone()
    return row[0] if row else 0


def table_exists(db: DatabaseManager, table_name: str) -> bool:
    """Return whether a table exists in the current SQLite schema."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def delete_fts_rows_for_conversation(
    db: DatabaseManager,
    conversation_id: str,
) -> None:
    """Delete message and record FTS rows tied to a conversation."""
    if table_exists(db, "messages_fts"):
        db.execute(
            "DELETE FROM messages_fts WHERE rowid IN "
            "(SELECT rowid FROM messages WHERE conversation_id = ?)",
            (conversation_id,),
        )
    if table_exists(db, "records_fts"):
        db.execute(
            "DELETE FROM records_fts WHERE rowid IN "
            "(SELECT rowid FROM records WHERE conversation_id = ?)",
            (conversation_id,),
        )


def parse_content(raw: str) -> str | list:
    """Parse stored message content into text or multimodal content."""
    if _looks_like_json_list(raw):
        parsed = _parse_json_list(raw)
        if parsed is not None:
            return parsed
    return raw


def _looks_like_json_list(raw: str) -> bool:
    """Return whether one stored content string may be a JSON list payload."""
    return bool(raw) and raw.startswith("[")


def _parse_json_list(raw: str) -> list | None:
    """Parse one JSON list string or return None."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, list):
        return parsed
    return None


def memory_weighted_search_limits(
    record_limit: int,
    summary_limit: int,
    message_search_limit: int,
    memory_query: bool,
    role_filter: str | None,
) -> tuple[int, int, int]:
    """Return query-aware search limits for context loading."""
    if memory_query:
        weighted_record_limit = min(max(record_limit * 2, 12), 40)
        weighted_summary_limit = min(max(summary_limit * 2, 8), 20)
        weighted_message_limit = max(3, message_search_limit // 2)
    else:
        weighted_record_limit = record_limit
        weighted_summary_limit = summary_limit
        weighted_message_limit = message_search_limit

    if role_filter:
        weighted_message_limit *= 2

    return (
        weighted_record_limit,
        weighted_summary_limit,
        weighted_message_limit,
    )
