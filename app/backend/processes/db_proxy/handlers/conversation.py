"""Conversation-related DB proxy handler factories."""

import uuid
from typing import TYPE_CHECKING, Any

from ..common import (
    OperationHandler,
    delete_fts_rows_for_conversation,
    table_exists,
    validate_params,
)
from ..params import (
    CreateConversationParams,
    DeleteConversationParams,
    ListConversationsParams,
    UpdateConversationProcessingStateParams,
    UpdateConversationTitleParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


def _conversation_item(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one conversation row into the API shape.

    Bridge/agent marking (``bridge_provider``) is added downstream by the web
    tier from ``list_chat_bridge_links`` — this handler returns base fields only.
    """
    return {
        "id": row[0],
        "title": row[1],
        "processing_status": row[2],
        "processing_stage": row[3],
        "processing_started_at": row[4],
        "created_at": row[5],
        "updated_at": row[6],
    }


def _conversation_payload(row: tuple[Any, ...] | None) -> dict[str, Any]:
    """Convert one optional conversation row into the API response shape."""
    if row is None:
        return {"conversation": None}
    return {"conversation": _conversation_item(row)}


def _conversation_row(
    db: DatabaseManager,
    conversation_id: str,
) -> tuple[Any, ...] | None:
    """Load one conversation row in the shared API column order."""
    return db.execute(
        "SELECT id, title, processing_status, processing_stage, "
        "processing_started_at, created_at, updated_at "
        "FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()


def make_create_conversation_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for create_conversation operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Create a new conversation."""
        p = validate_params(params, CreateConversationParams)
        # Callers may pin a conversation id (e.g. the scheduler's isolated
        # per-run conversation); otherwise generate one. INSERT OR IGNORE keeps
        # a repeated pinned id idempotent instead of failing on the primary key.
        conversation_id = p.conversation_id or str(uuid.uuid4())
        db.execute(
            "INSERT OR IGNORE INTO conversations (id, title) VALUES (?, ?)",
            (conversation_id, p.title),
        )
        db.commit()
        return {
            "conversation_id": conversation_id,
            **_conversation_payload(_conversation_row(db, conversation_id)),
        }

    return handle


def make_list_conversations_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_conversations operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List conversations ordered by update time."""
        p = validate_params(params, ListConversationsParams)
        rows = db.execute(
            "SELECT id, title, processing_status, processing_stage, "
            "processing_started_at, created_at, updated_at "
            "FROM conversations "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (p.limit, p.offset),
        ).fetchall()
        return {"conversations": [_conversation_item(row) for row in rows]}

    return handle


def make_delete_conversation_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for delete_conversation operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete a conversation and cascade to related data."""
        p = validate_params(params, DeleteConversationParams)
        delete_fts_rows_for_conversation(db, p.conversation_id)
        if table_exists(db, "record_embeddings"):
            db.execute(
                "DELETE FROM record_embeddings WHERE record_id IN "
                "(SELECT id FROM records WHERE conversation_id = ?)",
                (p.conversation_id,),
            )
        db.execute("DELETE FROM conversations WHERE id = ?", (p.conversation_id,))
        db.commit()
        db.execute("VACUUM")
        return {"ok": True}

    return handle


def make_update_conversation_title_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for update_conversation_title operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Update the title and modification time for one conversation."""
        p = validate_params(params, UpdateConversationTitleParams)
        db.execute(
            "UPDATE conversations "
            "SET title = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (p.title, p.conversation_id),
        )
        db.commit()
        return _conversation_payload(_conversation_row(db, p.conversation_id))

    return handle


def make_update_conversation_processing_state_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for update_conversation_processing_state operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Update the processing state for one conversation."""
        p = validate_params(params, UpdateConversationProcessingStateParams)
        db.execute(
            "UPDATE conversations "
            "SET processing_status = ?, processing_stage = ?, "
            "processing_started_at = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (
                p.processing_status,
                p.processing_stage,
                p.processing_started_at,
                p.conversation_id,
            ),
        )
        db.commit()
        return _conversation_payload(_conversation_row(db, p.conversation_id))

    return handle


def make_reset_stuck_processing_states_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for reset_stuck_processing_states operation."""

    async def handle(_params: dict[str, Any] | None) -> dict[str, Any]:
        """Reset every active processing row to idle on orchestrator startup."""
        cursor = db.execute(
            "UPDATE conversations "
            "SET processing_status = 'idle', processing_stage = '', "
            "processing_started_at = NULL, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE processing_status = 'active'"
        )
        db.commit()
        return {"reset_count": cursor.rowcount}

    return handle
