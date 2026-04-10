"""DB proxy handlers for per-conversation chat settings (key-value store).

Provides list and upsert operations for the chat_settings table.
Each row stores a single key-value pair scoped to a conversation.
Cascade delete on conversation removal is handled by the foreign key.
"""

from typing import Any

from backend.security.database import DatabaseManager

from ..common import OperationHandler, validate_params
from ..params import (
    ListChatSettingsParams,
    UpsertChatSettingsParams,
)


def make_list_chat_settings_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for list_chat_settings operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler that returns settings for one or all conversations.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return key-value settings, optionally filtered by conversation."""
        p = validate_params(params, ListChatSettingsParams)
        if p.conversation_id:
            cursor = db.execute(
                "SELECT key, value FROM chat_settings WHERE conversation_id = ?",
                (p.conversation_id,),
            )
            settings = {row[0]: row[1] for row in cursor.fetchall()}
            return {"settings": settings}
        cursor = db.execute("SELECT conversation_id, key, value FROM chat_settings")
        all_settings: dict[str, dict[str, str]] = {}
        for row in cursor.fetchall():
            cid = row[0]
            if cid not in all_settings:
                all_settings[cid] = {}
            all_settings[cid][row[1]] = row[2]
        return {"all_settings": all_settings}

    return handle


def make_upsert_chat_settings_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for upsert_chat_settings operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler that inserts or replaces settings for a conversation.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Insert or replace one or more key-value settings."""
        p = validate_params(params, UpsertChatSettingsParams)
        for entry in p.entries:
            db.execute(
                "INSERT INTO chat_settings (conversation_id, key, value, updated_at) "
                "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%f', 'now')) "
                "ON CONFLICT(conversation_id, key) DO UPDATE "
                "SET value = excluded.value, updated_at = excluded.updated_at",
                (p.conversation_id, entry.key, entry.value),
            )
        db.commit()
        cursor = db.execute(
            "SELECT key, value FROM chat_settings WHERE conversation_id = ?",
            (p.conversation_id,),
        )
        settings = {row[0]: row[1] for row in cursor.fetchall()}
        return {"settings": settings}

    return handle
