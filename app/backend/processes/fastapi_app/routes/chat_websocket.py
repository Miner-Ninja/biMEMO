"""WebSocket realtime helpers for chat route handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect


if TYPE_CHECKING:
    from backend.channels.jwt_auth import SessionManager, TokenClaims
    from backend.channels.sse import SSEConnection, SSEManager
    from backend.ipc.models import StreamEvent

    from ..clients import OrchestratorClient, SigningClient


logger = logging.getLogger(__name__)

WS_KEEPALIVE_INTERVAL = 5
WS_POLICY_VIOLATION_CLOSE_CODE = 1008
WS_SERVICE_RESTART_CLOSE_CODE = 1012
WS_TRY_AGAIN_LATER_CLOSE_CODE = 1013


def build_websocket_payload(event: StreamEvent) -> dict[str, object]:
    """Build one browser WebSocket event payload from an IPC stream event."""
    payload: dict[str, object] = {"type": event.event_type}
    if event.data is not None:
        payload["data"] = event.data
    if event.request_id is not None:
        payload["request_id"] = event.request_id
    return payload


async def authenticate_websocket(
    *,
    websocket: WebSocket,
    session_manager: SessionManager,
    signing_client: SigningClient | None,
    audit_fn: object,
) -> TokenClaims | None:
    """Authenticate one WebSocket handshake with the existing access cookie."""
    token = websocket.cookies.get("access_token")
    if not token or signing_client is None:
        return None
    return await session_manager.async_validate_token(
        token,
        signing_client,
        audit_fn=audit_fn,
    )


def register_websocket_session(
    *,
    session_id: str,
    session_exp: int,
    conversation_id: str,
    orchestrator_client: OrchestratorClient,
    sse_manager: SSEManager,
    session_manager: SessionManager,
) -> tuple[asyncio.Queue, SSEConnection]:
    """Register one WebSocket queue with the shared stream session managers."""
    queue: asyncio.Queue = asyncio.Queue()
    orchestrator_client.register_session_queue(
        session_id,
        queue,
        conversation_id=conversation_id,
    )
    connection = sse_manager.connect(session_id, session_exp, session_manager)
    if connection is not None:
        return queue, connection
    orchestrator_client.unregister_session_queue(session_id, queue)
    raise RuntimeError("Too many active realtime sessions")


def disconnect_websocket_session(
    *,
    session_id: str,
    queue: asyncio.Queue,
    connection: SSEConnection,
    orchestrator_client: OrchestratorClient,
    sse_manager: SSEManager,
) -> None:
    """Unregister one WebSocket stream session from IPC and session tracking."""
    orchestrator_client.unregister_session_queue(session_id, queue)
    sse_manager.disconnect(session_id, connection)


async def websocket_events(
    *,
    websocket: WebSocket,
    queue: asyncio.Queue,
    connection: SSEConnection,
    session_id: str,
    orchestrator_client: OrchestratorClient,
    sse_manager: SSEManager,
) -> None:
    """Send queued stream events to one connected WebSocket client."""
    try:
        while sse_manager.is_valid(session_id):
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=WS_KEEPALIVE_INTERVAL,
                )
                await websocket.send_json(build_websocket_payload(event))
            except TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for session %s", session_id)
    except RuntimeError:
        logger.debug(
            "WebSocket send failed for session %s",
            session_id,
            exc_info=True,
        )
    finally:
        disconnect_websocket_session(
            session_id=session_id,
            queue=queue,
            connection=connection,
            orchestrator_client=orchestrator_client,
            sse_manager=sse_manager,
        )
