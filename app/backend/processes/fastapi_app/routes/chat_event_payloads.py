"""Browser realtime event payload helpers for chat routes."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.models.responses import ConversationItem as HTTPConversationItem


def conversation_updated_event_data(
    conversation: HTTPConversationItem,
) -> dict[str, object]:
    """Build one lightweight browser event for a changed conversation."""
    return {
        "conversation_id": conversation.id,
        "title": conversation.title,
        "bridge_provider": conversation.bridge_provider,
        "settings": conversation.settings.model_dump(),
        "processing_status": conversation.processing_status,
        "processing_stage": conversation.processing_stage,
        "processing_started_at": conversation.processing_started_at,
        "updated_at": conversation.updated_at,
    }


def conversation_deleted_event_data(conversation_id: str) -> dict[str, str]:
    """Build one browser event for a removed conversation."""
    return {"conversation_id": conversation_id}


def conversation_created_event_data(
    conversation: HTTPConversationItem,
) -> dict[str, object]:
    """Build one browser event for a newly created conversation."""
    return conversation_updated_event_data(conversation)
