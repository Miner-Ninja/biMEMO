"""SSE streaming helpers for chat route handlers.

Provides functions for building SSE payloads, managing stream sessions,
and yielding real-time events to connected browser clients. Handles
keepalive heartbeats and session lifecycle (connect/disconnect) via
the SSEManager and OrchestratorClient IPC queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from backend.channels.jwt_auth import SessionManager
    from backend.channels.sse import SSEManager
    from backend.models.responses import ConversationItem as HTTPConversationItem

    from ..clients import OrchestratorClient


logger = logging.getLogger(__name__)

SSE_KEEPALIVE_INTERVAL = 5


def build_stream_headers() -> dict[str, str]:
    """Build the HTTP headers for one SSE chat stream."""
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def build_sse_payload(
    *,
    event_type: str,
    data: object | None = None,
    request_id: str | None = None,
) -> str:
    """Build one SSE data frame for the browser stream."""
    payload: dict[str, object] = {"type": event_type}
    if data is not None:
        payload["data"] = data
    if request_id is not None:
        payload["request_id"] = request_id
    return f"data: {json.dumps(payload)}\n\n"


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


def connect_stream_session(
    *,
    session_id: str,
    session_exp: int,
    conversation_id: str,
    queue: asyncio.Queue,
    orchestrator_client: OrchestratorClient,
    sse_manager: SSEManager,
    session_manager: SessionManager,
) -> None:
    """Register one stream queue and enforce the active-session limit."""
    orchestrator_client.register_session_queue(
        session_id,
        queue,
        conversation_id=conversation_id,
    )
    if sse_manager.connect(session_id, session_exp, session_manager) is not None:
        return
    orchestrator_client.unregister_session_queue(session_id)
    raise HTTPException(
        status_code=429,
        detail="Too many active sessions",
    )


def disconnect_stream_session(
    *,
    session_id: str,
    orchestrator_client: OrchestratorClient,
    sse_manager: SSEManager,
) -> None:
    """Unregister one browser stream session from IPC and SSE tracking."""
    orchestrator_client.unregister_session_queue(session_id)
    sse_manager.disconnect(session_id)


async def stream_events(
    *,
    queue: asyncio.Queue,
    session_id: str,
    orchestrator_client: OrchestratorClient,
    sse_manager: SSEManager,
) -> AsyncGenerator[str]:
    """Yield SSE frames for one connected browser session."""
    try:
        while sse_manager.is_valid(session_id):
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=SSE_KEEPALIVE_INTERVAL,
                )
                yield build_sse_payload(
                    event_type=event.event_type,
                    data=event.data,
                    request_id=event.request_id,
                )
            except TimeoutError:
                yield build_sse_payload(event_type="heartbeat")
    finally:
        disconnect_stream_session(
            session_id=session_id,
            orchestrator_client=orchestrator_client,
            sse_manager=sse_manager,
        )
