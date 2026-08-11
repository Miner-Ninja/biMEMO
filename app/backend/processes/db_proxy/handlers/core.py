"""Audit handler factories and backward-compatible re-exports.

Contains make_write_audit_handler and make_verify_chain_handler for the
tamper-evident audit log hash chain. Also re-exports all handler factories
from message_handlers and memory_handlers so that existing imports from
this module continue to work without changes.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

from ..common import (
    AUDIT_INITIALIZATION_VECTOR,
    HASH_ANCHOR_INTERVAL,
    OperationHandler,
    build_in_clause_query,
    compute_audit_hash,
    table_exists,
    validate_params,
)
from ..params import (
    WriteAuditParams,
)
from .memory import (
    make_bump_queued_record_attempts_handler,
    make_delete_queued_records_handler,
    make_fetch_unembedded_records_handler,
    make_get_vector_coverage_handler,
    make_init_vector_table_handler,
    make_list_queued_records_handler,
    make_peek_record_queue_handler,
    make_prune_unlinked_records_handler,
    make_save_queued_record_handler,
    make_save_record_handler,
    make_store_embedding_handler,
    make_vector_search_handler,
)
from .message import (
    make_fulltext_search_handler,
    make_load_context_handler,
    make_load_recent_messages_handler,
    make_save_conversation_summary_handler,
    make_save_message_handler,
)


if TYPE_CHECKING:
    from pydantic import BaseModel

    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)


def _validate_params(
    params: dict[str, Any] | None,
    model_class: type[BaseModel],
) -> BaseModel:
    """Validate operation parameters against a Pydantic model."""
    return validate_params(params, model_class)


def _compute_audit_hash(
    previous_hash: str,
    timestamp: float,
    event_type: str,
    tool_name: str | None,
    parameters: str | None,
    result: str | None,
    zone: str | None,
    duration_ms: float | None,
) -> str:
    """Compute SHA-256 hash for an audit log entry.

    Args:
        previous_hash: Hash of the previous entry.
        timestamp: Unix timestamp.
        event_type: Event type string.
        tool_name: Tool name or None.
        parameters: JSON parameters or None.
        result: Result string or None.
        zone: Trust zone or None.
        duration_ms: Duration in milliseconds or None.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    return compute_audit_hash(
        previous_hash,
        timestamp,
        event_type,
        tool_name,
        parameters,
        result,
        zone,
        duration_ms,
    )


def _build_in_clause_query(base_sql: str, item_count: int) -> str:
    """Build a SQL query with a validated ``IN (?, ?, ...)`` clause."""
    return build_in_clause_query(base_sql, item_count)


def _count_rows_for_table(
    db: DatabaseManager,
    table_name: str,
    query_map: dict[str, str],
) -> int:
    """Count rows for a known table using a fixed SQL mapping."""
    from ..common import count_rows_for_table

    return count_rows_for_table(db, table_name, query_map)


def _table_exists(db: DatabaseManager, table_name: str) -> bool:
    """Return whether a table exists in the current SQLite schema."""
    return table_exists(db, table_name)


def _delete_fts_rows_for_conversation(
    db: DatabaseManager,
    conversation_id: str,
) -> None:
    """Delete message and record FTS rows tied to a conversation."""
    if _table_exists(db, "messages_fts"):
        db.execute(
            "DELETE FROM messages_fts WHERE rowid IN "
            "(SELECT rowid FROM messages WHERE conversation_id = ?)",
            (conversation_id,),
        )
    if _table_exists(db, "records_fts"):
        db.execute(
            "DELETE FROM records_fts WHERE rowid IN "
            "(SELECT rowid FROM records WHERE conversation_id = ?)",
            (conversation_id,),
        )


def make_write_audit_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for write_audit operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Append an audit log entry with hash chain."""
        p = _validate_params(params, WriteAuditParams)
        timestamp = time.time()

        cursor = db.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            previous_hash = hashlib.sha256(
                AUDIT_INITIALIZATION_VECTOR.encode()
            ).hexdigest()
        else:
            previous_hash = row[0]

        entry_hash = _compute_audit_hash(
            previous_hash,
            timestamp,
            p.event_type,
            p.tool_name,
            p.parameters,
            p.result,
            p.zone,
            p.duration_ms,
        )

        cursor = db.execute(
            "INSERT INTO audit_log "
            "(timestamp, event_type, tool_name, parameters, "
            "result, zone, duration_ms, previous_hash, entry_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp,
                p.event_type,
                p.tool_name,
                p.parameters,
                p.result,
                p.zone,
                p.duration_ms,
                previous_hash,
                entry_hash,
            ),
        )
        db.commit()

        entry_id = cursor.lastrowid
        if entry_id is not None and entry_id % HASH_ANCHOR_INTERVAL == 0:
            logger.info(
                "AUDIT ANCHOR: entry_id=%d hash=%s",
                entry_id,
                entry_hash,
            )

        return {"entry_id": entry_id}

    return handle


def make_verify_chain_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for verify_chain operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Verify audit log hash chain integrity."""
        expected_prev = hashlib.sha256(AUDIT_INITIALIZATION_VECTOR.encode()).hexdigest()

        cursor = db.execute(
            "SELECT id, timestamp, event_type, tool_name, "
            "parameters, result, zone, duration_ms, "
            "previous_hash, entry_hash "
            "FROM audit_log ORDER BY id ASC"
        )

        for row in cursor:
            (
                entry_id,
                timestamp,
                event_type,
                tool_name,
                parameters,
                result,
                zone,
                duration_ms,
                previous_hash,
                entry_hash,
            ) = row

            if previous_hash != expected_prev:
                return {"valid": False, "broken_at": entry_id}

            computed = _compute_audit_hash(
                previous_hash,
                timestamp,
                event_type,
                tool_name,
                parameters,
                result,
                zone,
                duration_ms,
            )
            if computed != entry_hash:
                return {"valid": False, "tampered_at": entry_id}

            expected_prev = entry_hash

        return {"valid": True}

    return handle


# --- Backward-compatible re-exports ---
__all__ = [
    "make_bump_queued_record_attempts_handler",
    "make_delete_queued_records_handler",
    "make_fetch_unembedded_records_handler",
    "make_fulltext_search_handler",
    "make_get_vector_coverage_handler",
    "make_init_vector_table_handler",
    "make_list_queued_records_handler",
    "make_load_context_handler",
    "make_load_recent_messages_handler",
    "make_peek_record_queue_handler",
    "make_prune_unlinked_records_handler",
    "make_save_conversation_summary_handler",
    "make_save_message_handler",
    "make_save_queued_record_handler",
    "make_save_record_handler",
    "make_store_embedding_handler",
    "make_vector_search_handler",
    "make_verify_chain_handler",
    "make_write_audit_handler",
]
