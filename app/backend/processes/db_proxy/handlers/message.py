"""Handler factories for message persistence, context loading, and fulltext search.

Provides make_save_message_handler, make_load_recent_messages_handler,
make_fulltext_search_handler, and make_load_context_handler. These handlers
manage the message lifecycle and the hybrid search-augmented context retrieval
pipeline used by the orchestrator.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from backend.memory.retrieval.fulltext import (
    index_message,
)

from ..common import (
    OperationHandler,
    build_in_clause_query,
    parse_content,
    table_exists,
    validate_params,
)
from ..params import (
    FulltextSearchParams,
    LoadContextParams,
    LoadRecentMessagesParams,
    SaveConversationSummaryParams,
    SaveMessageParams,
)


if TYPE_CHECKING:
    from pydantic import BaseModel

    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

# Cap for message content returned by fulltext_search hits. Full text of a
# hit is available via get_memory; search results only need enough to read.
SEARCH_RESULT_CONTENT_CHARS = 2000


def _validate_params(
    params: dict[str, Any] | None,
    model_class: type[BaseModel],
) -> BaseModel:
    """Validate operation parameters against a Pydantic model."""
    return validate_params(params, model_class)


def _build_in_clause_query(base_sql: str, item_count: int) -> str:
    """Build a SQL query with a validated ``IN (?, ?, ...)`` clause."""
    return build_in_clause_query(base_sql, item_count)


def _table_exists(db: DatabaseManager, table_name: str) -> bool:
    """Return whether a table exists in the current SQLite schema."""
    return table_exists(db, table_name)


def _load_conversation_summary(
    db: DatabaseManager,
    conversation_id: str,
) -> str:
    """Load the persisted S-zone summary for one conversation."""
    row = db.execute(
        "SELECT conversation_summary FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "")


def _load_base_context_rows(
    db: DatabaseManager,
    p: LoadContextParams,
) -> tuple[list[tuple], list[tuple]]:
    """Load the baseline records and chronological messages."""
    return (
        db.execute(
            "SELECT id, summary, key_facts, action_items, created_at FROM records "
            "WHERE tainted = 0 OR session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (p.session_id, p.record_limit),
        ).fetchall(),
        db.execute(
            "SELECT id, role, content, conversation_id, created_at FROM messages "
            "WHERE conversation_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (p.conversation_id, p.message_limit),
        ).fetchall(),
    )


def _build_base_context_result(
    *,
    records: list[tuple],
    messages: list[tuple],
) -> dict[str, Any]:
    """Build the baseline context payload."""
    return {
        "records": [
            {
                "id": record[0],
                "summary": record[1],
                "key_facts": record[2],
                "action_items": record[3],
                "created_at": record[4],
            }
            for record in records
        ],
        "messages": [
            {"role": message[1], "content": message[2]} for message in messages
        ],
    }


def _log_base_context_load(
    *,
    session_id: str,
    records: list[tuple],
    messages: list[tuple],
) -> None:
    """Log the size of the baseline context load."""
    logger.debug(
        "load_context: session=%s, obs=%d, msgs=%d",
        session_id[:8],
        len(records),
        len(messages),
    )


def _serialize_message_content(
    content: str | list[dict[str, Any]],
) -> tuple[str, str]:
    """Serialize one stored message payload and extract plain text for FTS."""
    if isinstance(content, list):
        return (
            json.dumps(content),
            " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ),
        )
    return content, content


def _resolve_next_conversation_title(
    *,
    db: DatabaseManager,
    conversation_id: str,
    role: str,
    content: str | list[dict[str, Any]],
) -> str:
    """Resolve the conversation title after saving one new message."""
    existing_conversation = db.execute(
        "SELECT title FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    current_title = str(existing_conversation[0]) if existing_conversation else ""
    next_title = current_title.strip()
    if next_title or role != "user" or not isinstance(content, str):
        return next_title

    collapsed_content = " ".join(content.split()).strip()
    if collapsed_content:
        return collapsed_content[:80]
    return next_title


def make_save_message_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_message operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Save a message to the database."""
        p = _validate_params(params, SaveMessageParams)
        message_id = str(uuid.uuid4())
        db_content, text_for_index = _serialize_message_content(p.content)
        next_title = _resolve_next_conversation_title(
            db=db,
            conversation_id=p.conversation_id,
            role=p.role,
            content=p.content,
        )
        db.execute(
            "INSERT OR IGNORE INTO conversations (id, title) VALUES (?, ?)",
            (p.conversation_id, next_title),
        )
        cursor = db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, channel, meta) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, p.conversation_id, p.role, db_content, p.channel, p.meta),
        )
        db.execute(
            "UPDATE conversations "
            "SET title = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (next_title, p.conversation_id),
        )
        db.commit()
        index_message(db, cursor.lastrowid, text_for_index)
        db.commit()
        return {"message_id": message_id}

    return handle


def make_load_recent_messages_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for load_recent_messages operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load recent messages for a conversation."""
        p = _validate_params(params, LoadRecentMessagesParams)
        rows = db.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (p.conversation_id, p.limit),
        ).fetchall()
        return {
            "messages": [
                {"role": row[0], "content": parse_content(row[1])} for row in rows
            ]
        }

    return handle


def make_fulltext_search_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for fulltext_search operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Search messages, records, or summaries via FTS5.

        Returns the ORIGINAL source text for each hit. The FTS index
        stores pre-stemmed tokens, so FTS snippets would surface stems
        ("clean ob") instead of readable text — every consumer needs
        the real row content.
        """
        from backend.memory.retrieval.fulltext import sanitize_fts_query

        p = _validate_params(params, FulltextSearchParams)
        safe_query = sanitize_fts_query(p.query)
        if not safe_query:
            return {"results": []}
        # Join the base table to return the stable text ``id`` alongside the
        # FTS rowid, so an agent can pass a search result to get_memory.
        if p.table == "messages":
            rows = db.execute(
                "SELECT m.id, messages_fts.rowid, m.role, "
                "substr(m.content, 1, ?), m.created_at "
                "FROM messages_fts "
                "JOIN messages m ON m.rowid = messages_fts.rowid "
                "WHERE messages_fts MATCH ? "
                "ORDER BY rank LIMIT ? OFFSET ?",
                (SEARCH_RESULT_CONTENT_CHARS, safe_query, p.limit, p.offset),
            ).fetchall()
            results = [
                {
                    "id": r[0],
                    "rowid": r[1],
                    "role": r[2],
                    "content": r[3],
                    "created_at": r[4],
                }
                for r in rows
            ]
        elif p.table == "records":
            # Tainted records are barred from search results, mirroring
            # their exclusion from distillation.
            rows = db.execute(
                "SELECT r.id, records_fts.rowid, r.summary, r.created_at "
                "FROM records_fts "
                "JOIN records r ON r.rowid = records_fts.rowid "
                "WHERE records_fts MATCH ? AND r.tainted = 0 "
                "ORDER BY rank LIMIT ? OFFSET ?",
                (safe_query, p.limit, p.offset),
            ).fetchall()
            results = [
                {
                    "id": r[0],
                    "rowid": r[1],
                    "summary": r[2],
                    "created_at": r[3],
                }
                for r in rows
            ]
        else:
            raise ValueError(f"Invalid search table: '{p.table}'")
        return {"results": results}

    return handle


def make_load_context_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for load_context operation.

    Baseline context only: recent records (dialog-recorder dedup source)
    plus the conversation's chronological messages and its persisted
    S-zone summary. Query-time retrieval lives in the graph operations.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load baseline context: records, messages, S-zone summary."""
        p = _validate_params(params, LoadContextParams)
        records, messages = _load_base_context_rows(
            db,
            p,
        )
        result = _build_base_context_result(
            records=records,
            messages=messages,
        )
        result["conversation_summary"] = _load_conversation_summary(
            db,
            p.conversation_id,
        )
        _log_base_context_load(
            session_id=p.session_id,
            records=records,
            messages=messages,
        )
        return result

    return handle


def make_save_conversation_summary_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for save_conversation_summary operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist the S-zone conversation summary for one dialog."""
        p = _validate_params(params, SaveConversationSummaryParams)
        db.execute(
            "UPDATE conversations SET conversation_summary = ? WHERE id = ?",
            (p.summary, p.conversation_id),
        )
        db.commit()
        return {"saved": True}

    return handle
