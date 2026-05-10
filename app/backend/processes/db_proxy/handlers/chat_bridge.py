"""DB proxy handlers for external chat bridge links and message mappings."""

import uuid
from typing import TYPE_CHECKING, Any

from ..common import OperationHandler, validate_params
from ..params import (
    CreateChatBridgeLinkParams,
    CreateChatBridgeMessageLinkParams,
    DisableChatBridgeLinkParams,
    EnableChatBridgeLinkParams,
    GetChatBridgeByExternalTargetParams,
    GetChatBridgeForConversationParams,
    GetChatBridgeLinkParams,
    GetChatBridgeMessageLinkByExternalMessageParams,
    GetChatBridgeMessageLinkByMessageIdParams,
    ListChatBridgeLinksParams,
    ListChatBridgeMessageLinksParams,
    UpdateChatBridgeLinkSettingsParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL = (
    "SELECT id, conversation_id, provider, external_space_id, "
    "external_channel_id, created_by, sync_direction, status, "
    "delivery_mode, persona_mode, persona_user_id, created_at "
    "FROM chat_bridge_links"
)
SELECT_CHAT_BRIDGE_MESSAGE_LINK_COLUMNS_SQL = (
    "SELECT id, bridge_link_id, message_id, external_message_id, "
    "direction, created_at "
    "FROM chat_bridge_message_links"
)


def _bridge_link_from_row(row: tuple[Any, ...]) -> dict[str, object]:
    """Convert one chat bridge link row into a response payload."""
    return {
        "id": row[0],
        "conversation_id": row[1],
        "provider": row[2],
        "external_space_id": row[3],
        "external_channel_id": row[4],
        "created_by": row[5],
        "sync_direction": row[6],
        "status": row[7],
        "delivery_mode": row[8],
        "persona_mode": row[9],
        "persona_user_id": row[10],
        "created_at": row[11],
    }


def _chat_bridge_link_result(row: tuple[Any, ...] | None) -> dict[str, object]:
    """Build one chat-bridge lookup result from an optional row."""
    if row is None:
        return {"chat_bridge_link": None}
    return {"chat_bridge_link": _bridge_link_from_row(row)}


def _bridge_message_link_from_row(row: tuple[Any, ...]) -> dict[str, object]:
    """Convert one bridge message mapping row into a response payload."""
    return {
        "id": row[0],
        "bridge_link_id": row[1],
        "message_id": row[2],
        "external_message_id": row[3],
        "direction": row[4],
        "created_at": row[5],
    }


def _chat_bridge_message_link_result(row: tuple[Any, ...] | None) -> dict[str, object]:
    """Build one bridge-message lookup result from an optional row."""
    if row is None:
        return {"chat_bridge_message_link": None}
    return {"chat_bridge_message_link": _bridge_message_link_from_row(row)}


def _chat_bridge_link_rows(
    db: DatabaseManager,
    *,
    provider: str,
    conversation_id: str,
    limit: int,
    offset: int,
) -> list[tuple[Any, ...]]:
    """Load bridge-link rows with optional provider and conversation filters."""
    if provider and conversation_id:
        return db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} "
            "WHERE provider = ? AND conversation_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (provider, conversation_id, limit, offset),
        ).fetchall()
    if provider:
        return db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} "
            "WHERE provider = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (provider, limit, offset),
        ).fetchall()
    if conversation_id:
        return db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} "
            "WHERE conversation_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (conversation_id, limit, offset),
        ).fetchall()
    return db.execute(
        f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} "
        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()


def make_create_chat_bridge_link_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for create_chat_bridge_link operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist one external chat bridge link."""
        p = validate_params(params, CreateChatBridgeLinkParams)
        bridge_link_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO chat_bridge_links ("
            "id, conversation_id, provider, external_space_id, "
            "external_channel_id, created_by, sync_direction, status, "
            "delivery_mode, persona_mode, persona_user_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bridge_link_id,
                p.conversation_id,
                p.provider,
                p.external_space_id,
                p.external_channel_id,
                p.created_by,
                p.sync_direction,
                p.status,
                p.delivery_mode,
                p.persona_mode,
                p.persona_user_id,
            ),
        )
        db.commit()
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} WHERE id = ?",
            (bridge_link_id,),
        ).fetchone()
        return {"chat_bridge_link": _bridge_link_from_row(row)}

    return handle


def make_list_chat_bridge_links_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for list_chat_bridge_links operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List stored bridge links with optional provider filters."""
        p = validate_params(params, ListChatBridgeLinksParams)
        rows = _chat_bridge_link_rows(
            db,
            provider=p.provider,
            conversation_id=p.conversation_id,
            limit=p.limit,
            offset=p.offset,
        )
        return {"chat_bridge_links": [_bridge_link_from_row(row) for row in rows]}

    return handle


def make_get_chat_bridge_for_conversation_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_chat_bridge_for_conversation operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load one bridge link by conversation identifier."""
        p = validate_params(params, GetChatBridgeForConversationParams)
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} WHERE conversation_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (p.conversation_id,),
        ).fetchone()
        return _chat_bridge_link_result(row)

    return handle


def make_get_chat_bridge_link_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_chat_bridge_link operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load one bridge link by its stored identifier."""
        p = validate_params(params, GetChatBridgeLinkParams)
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} WHERE id = ? LIMIT 1",
            (p.bridge_link_id,),
        ).fetchone()
        return _chat_bridge_link_result(row)

    return handle


def make_get_chat_bridge_by_external_target_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_chat_bridge_by_external_target operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load one bridge link by provider-side identifiers."""
        p = validate_params(params, GetChatBridgeByExternalTargetParams)
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} "
            "WHERE provider = ? AND external_space_id = ? AND external_channel_id = ? "
            "LIMIT 1",
            (p.provider, p.external_space_id, p.external_channel_id),
        ).fetchone()
        return _chat_bridge_link_result(row)

    return handle


def make_disable_chat_bridge_link_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for disable_chat_bridge_link operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Disable one bridge link without deleting its history."""
        p = validate_params(params, DisableChatBridgeLinkParams)
        cursor = db.execute(
            "UPDATE chat_bridge_links "
            "SET status = 'disabled', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (p.bridge_link_id,),
        )
        db.commit()
        return {"updated": cursor.rowcount > 0}

    return handle


def make_enable_chat_bridge_link_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for enable_chat_bridge_link operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Enable one bridge link without creating a replacement row."""
        p = validate_params(params, EnableChatBridgeLinkParams)
        cursor = db.execute(
            "UPDATE chat_bridge_links "
            "SET status = 'active', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (p.bridge_link_id,),
        )
        db.commit()
        return {"updated": cursor.rowcount > 0}

    return handle


def make_update_chat_bridge_link_settings_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for update_chat_bridge_link_settings operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Update one stored bridge link presentation mode."""
        p = validate_params(params, UpdateChatBridgeLinkSettingsParams)
        cursor = db.execute(
            "UPDATE chat_bridge_links "
            "SET delivery_mode = ?, persona_mode = ?, persona_user_id = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (
                p.delivery_mode,
                p.persona_mode,
                p.persona_user_id,
                p.bridge_link_id,
            ),
        )
        db.commit()
        if cursor.rowcount <= 0:
            return {"updated": False, "chat_bridge_link": None}
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_LINK_COLUMNS_SQL} WHERE id = ? LIMIT 1",
            (p.bridge_link_id,),
        ).fetchone()
        return {
            "updated": True,
            "chat_bridge_link": _bridge_link_from_row(row),
        }

    return handle


def make_create_chat_bridge_message_link_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for create_chat_bridge_message_link operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist one bridge message mapping."""
        p = validate_params(params, CreateChatBridgeMessageLinkParams)
        mapping_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO chat_bridge_message_links ("
            "id, bridge_link_id, message_id, external_message_id, direction"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                mapping_id,
                p.bridge_link_id,
                p.message_id,
                p.external_message_id,
                p.direction,
            ),
        )
        db.commit()
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_MESSAGE_LINK_COLUMNS_SQL} WHERE id = ?",
            (mapping_id,),
        ).fetchone()
        return {"chat_bridge_message_link": _bridge_message_link_from_row(row)}

    return handle


def make_list_chat_bridge_message_links_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for list_chat_bridge_message_links operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List stored bridge message mappings for one bridge link."""
        p = validate_params(params, ListChatBridgeMessageLinksParams)
        if p.direction:
            rows = db.execute(
                f"{SELECT_CHAT_BRIDGE_MESSAGE_LINK_COLUMNS_SQL} "
                "WHERE bridge_link_id = ? AND direction = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (p.bridge_link_id, p.direction, p.limit, p.offset),
            ).fetchall()
        else:
            rows = db.execute(
                f"{SELECT_CHAT_BRIDGE_MESSAGE_LINK_COLUMNS_SQL} "
                "WHERE bridge_link_id = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (p.bridge_link_id, p.limit, p.offset),
            ).fetchall()
        return {
            "chat_bridge_message_links": [
                _bridge_message_link_from_row(row) for row in rows
            ]
        }

    return handle


def make_get_chat_bridge_message_link_by_external_message_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_chat_bridge_message_link_by_external_message."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load one bridge message mapping by provider-side message identifier."""
        p = validate_params(params, GetChatBridgeMessageLinkByExternalMessageParams)
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_MESSAGE_LINK_COLUMNS_SQL} "
            "WHERE bridge_link_id = ? AND external_message_id = ? LIMIT 1",
            (p.bridge_link_id, p.external_message_id),
        ).fetchone()
        return _chat_bridge_message_link_result(row)

    return handle


def make_get_chat_bridge_message_link_by_message_id_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_chat_bridge_message_link_by_message_id."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load one bridge message mapping by internal message identifier."""
        p = validate_params(params, GetChatBridgeMessageLinkByMessageIdParams)
        row = db.execute(
            f"{SELECT_CHAT_BRIDGE_MESSAGE_LINK_COLUMNS_SQL} "
            "WHERE bridge_link_id = ? AND message_id = ? LIMIT 1",
            (p.bridge_link_id, p.message_id),
        ).fetchone()
        return _chat_bridge_message_link_result(row)

    return handle
