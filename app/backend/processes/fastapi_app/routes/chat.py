"""Chat, WebSocket, stop, confirm routes."""

# ruff: noqa: B008

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request, WebSocket

from backend.ipc.models import ConfirmRequest as IPCConfirmRequest
from backend.ipc.models import StopRequest as IPCStopRequest
from backend.ipc.models import StreamEventType
from backend.models.requests import ChatRequest as HTTPChatRequest  # noqa: TC001
from backend.models.requests import ChatSettingsUpdateRequest  # noqa: TC001
from backend.models.requests import ConfirmRequest as HTTPConfirmRequest  # noqa: TC001
from backend.models.requests import (
    ConversationCreateRequest as HTTPConversationCreateRequest,  # noqa: TC001
)
from backend.models.requests import (
    ConversationUpdateRequest as HTTPConversationUpdateRequest,  # noqa: TC001
)
from backend.models.requests import StopRequest as HTTPStopRequest  # noqa: TC001
from backend.models.responses import ChatSettingsResponse
from backend.models.responses import ChatStartResponse as HTTPChatStartResponse
from backend.models.responses import (
    ConversationCreateResponse as HTTPConversationCreateResponse,
)
from backend.models.responses import (
    ConversationDeleteResponse as HTTPConversationDeleteResponse,
)
from backend.models.responses import ConversationItem as HTTPConversationItem
from backend.models.responses import (
    ConversationMessageItem as HTTPConversationMessageItem,
)
from backend.models.responses import StopResponse as HTTPStopResponse

from ..deps import (
    _get_admin_client,
    _get_optional_chat_bridge_service,
    _get_orchestrator_client,
    _require_auth,
)
from .chat_event_payloads import (
    conversation_created_event_data,
    conversation_deleted_event_data,
    conversation_updated_event_data,
)
from .chat_helpers import (
    DEFAULT_CONVERSATION_LIST_LIMIT,
    DEFAULT_CONVERSATION_MESSAGES_LIMIT,
    build_busy_chat_response,
    build_confirm_response,
    chat_settings_from_raw,
    create_persisted_conversation,
    delete_conversation_item,
    list_conversation_items,
    list_conversation_message_items,
    send_ipc_control_request,
    start_web_chat_request,
    update_conversation_item,
)
from .chat_websocket import (
    WS_POLICY_VIOLATION_CLOSE_CODE,
    WS_SERVICE_RESTART_CLOSE_CODE,
    WS_TRY_AGAIN_LATER_CLOSE_CODE,
    authenticate_websocket,
    register_websocket_session,
    websocket_events,
)


if TYPE_CHECKING:
    from backend.channels.jwt_auth import SessionManager, TokenClaims
    from backend.channels.realtime import RealtimeConnectionManager
    from backend.chat_bridge import ChatBridgeService

    from ..clients import AdminDBClient, OrchestratorClient


chat_router = APIRouter()


@chat_router.post("/chat")
async def chat(
    body: HTTPChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    claims: TokenClaims = Depends(_require_auth),
    orchestrator_client: OrchestratorClient = Depends(
        _get_orchestrator_client
    ),
    admin_client: AdminDBClient = Depends(_get_admin_client),
    chat_bridge_service: ChatBridgeService | None = Depends(
        _get_optional_chat_bridge_service
    ),
) -> HTTPChatStartResponse | dict:
    """Process a chat message via Orchestrator IPC.

    Returns 202 when the Orchestrator is already processing.

    Args:
        body: Chat request with message text.
        request: FastAPI request.
        background_tasks: Background task scheduler for post-response work.
        claims: Authenticated token claims.
        orchestrator_client: Orchestrator IPC client.
        admin_client: Database client for file metadata lookup.
        chat_bridge_service: Optional chat bridge service for auto-created links.

    Returns:
        ChatResponse or 202 accepted.
    """
    if orchestrator_client.is_processing:
        return build_busy_chat_response()

    return await start_web_chat_request(
        body=body,
        request=request,
        claims=claims,
        orchestrator_client=orchestrator_client,
        admin_client=admin_client,
        chat_bridge_service=chat_bridge_service,
        background_tasks=background_tasks,
    )


@chat_router.post("/chat/conversations")
async def create_conversation(
    body: HTTPConversationCreateRequest,
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
    admin_client: AdminDBClient = Depends(_get_admin_client),
    orchestrator_client: OrchestratorClient = Depends(
        _get_orchestrator_client
    ),
    chat_bridge_service: ChatBridgeService | None = Depends(
        _get_optional_chat_bridge_service
    ),
) -> HTTPConversationCreateResponse:
    """Create one empty persisted conversation."""
    conversation = await create_persisted_conversation(
        title=body.title,
        admin_client=admin_client,
    )
    await orchestrator_client.publish_conversation_event(
        conversation_id=conversation.id,
        event_type=StreamEventType.CONVERSATION_CREATED,
        data=conversation_created_event_data(conversation),
    )
    return HTTPConversationCreateResponse(conversation_id=conversation.id)


@chat_router.get("/chat/conversations")
async def list_conversations(
    _claims: TokenClaims = Depends(_require_auth),
    admin_client: AdminDBClient = Depends(_get_admin_client),
    limit: int = DEFAULT_CONVERSATION_LIST_LIMIT,
    offset: int = 0,
) -> list[HTTPConversationItem]:
    """List persisted conversations ordered by most recent activity."""
    return await list_conversation_items(
        admin_client=admin_client,
        limit=limit,
        offset=offset,
    )


@chat_router.get("/chat/conversations/{conversation_id}/messages")
async def list_conversation_messages(
    conversation_id: str,
    _claims: TokenClaims = Depends(_require_auth),
    admin_client: AdminDBClient = Depends(_get_admin_client),
    limit: int = DEFAULT_CONVERSATION_MESSAGES_LIMIT,
    offset: int = 0,
) -> list[HTTPConversationMessageItem]:
    """List persisted messages for one conversation."""
    return await list_conversation_message_items(
        admin_client=admin_client,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
    )


@chat_router.put("/chat/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: HTTPConversationUpdateRequest,
    _claims: TokenClaims = Depends(_require_auth),
    admin_client: AdminDBClient = Depends(_get_admin_client),
    orchestrator_client: OrchestratorClient = Depends(
        _get_orchestrator_client
    ),
) -> HTTPConversationItem:
    """Update one persisted conversation title."""
    conversation = await update_conversation_item(
        admin_client=admin_client,
        conversation_id=conversation_id,
        title=body.title,
    )
    await orchestrator_client.publish_conversation_event(
        conversation_id=conversation_id,
        event_type=StreamEventType.CONVERSATION_UPDATED,
        data=conversation_updated_event_data(conversation),
    )
    return conversation


@chat_router.patch("/chat/conversations/{conversation_id}/settings")
async def update_conversation_settings(
    conversation_id: str,
    body: ChatSettingsUpdateRequest,
    _claims: TokenClaims = Depends(_require_auth),
    admin_client: AdminDBClient = Depends(_get_admin_client),
    orchestrator_client: OrchestratorClient = Depends(
        _get_orchestrator_client
    ),
) -> ChatSettingsResponse:
    """Update per-conversation display settings."""
    entries: list[dict[str, str]] = []
    if body.show_sql is not None:
        entries.append({"key": "show_sql", "value": "1" if body.show_sql else "0"})
    if body.show_thinking is not None:
        entries.append(
            {"key": "show_thinking", "value": "1" if body.show_thinking else "0"}
        )
    if entries:
        raw = await admin_client.upsert_chat_settings(conversation_id, entries)
    else:
        raw = await admin_client.list_chat_settings(conversation_id)
    settings = chat_settings_from_raw(raw)
    await orchestrator_client.publish_conversation_event(
        conversation_id=conversation_id,
        event_type=StreamEventType.CONVERSATION_UPDATED,
        data={"conversation_id": conversation_id, "settings": settings.model_dump()},
    )
    return settings


@chat_router.delete("/chat/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    _claims: TokenClaims = Depends(_require_auth),
    admin_client: AdminDBClient = Depends(_get_admin_client),
    orchestrator_client: OrchestratorClient = Depends(
        _get_orchestrator_client
    ),
) -> HTTPConversationDeleteResponse:
    """Delete one persisted conversation and related data."""
    response = await delete_conversation_item(
        admin_client=admin_client,
        conversation_id=conversation_id,
    )
    await orchestrator_client.publish_conversation_event(
        conversation_id=conversation_id,
        event_type=StreamEventType.CONVERSATION_DELETED,
        data=conversation_deleted_event_data(conversation_id),
    )
    return response


@chat_router.websocket("/ws")
async def websocket_stream(
    websocket: WebSocket,
    conversation_id: str = "",
) -> None:
    """Establish WebSocket connection for realtime stream events."""
    session_manager: SessionManager = websocket.app.state.session_manager
    realtime_manager: RealtimeConnectionManager = websocket.app.state.realtime_manager
    orchestrator_client: OrchestratorClient = websocket.app.state.orchestrator_client
    signing_client = websocket.app.state.signing_client
    audit_fn = websocket.app.state.audit_fn

    claims = await authenticate_websocket(
        websocket=websocket,
        session_manager=session_manager,
        signing_client=signing_client,
        audit_fn=audit_fn,
    )
    if claims is None:
        await websocket.accept()
        await websocket.close(code=WS_POLICY_VIOLATION_CLOSE_CODE)
        return
    if not orchestrator_client.is_connected:
        await websocket.accept()
        await websocket.close(code=WS_SERVICE_RESTART_CLOSE_CODE)
        return
    try:
        queue, connection = register_websocket_session(
            session_id=claims.jti,
            session_exp=claims.exp,
            conversation_id=conversation_id,
            orchestrator_client=orchestrator_client,
            realtime_manager=realtime_manager,
            session_manager=session_manager,
        )
    except RuntimeError:
        await websocket.accept()
        await websocket.close(code=WS_TRY_AGAIN_LATER_CLOSE_CODE)
        return

    await websocket.accept()
    await websocket_events(
        websocket=websocket,
        queue=queue,
        connection=connection,
        session_id=claims.jti,
        orchestrator_client=orchestrator_client,
        realtime_manager=realtime_manager,
    )


@chat_router.post("/stop")
async def stop(
    request: Request,
    body: HTTPStopRequest | None = Body(default=None),
    claims: TokenClaims = Depends(_require_auth),
    orchestrator_client: OrchestratorClient = Depends(
        _get_orchestrator_client
    ),
) -> HTTPStopResponse:
    """Stop the active agentic loop via Orchestrator IPC.

    Args:
        request: FastAPI request.
        body: Optional stop payload with the active chat request ID.
        claims: Authenticated token claims.
        orchestrator_client: Orchestrator IPC client.

    Returns:
        StopResponse indicating whether a loop was stopped.
    """
    request_id = body.request_id.strip() if body is not None else ""
    if not request_id:
        request_id = str(uuid.uuid4())
    ipc_request = IPCStopRequest(request_id=request_id)
    ipc_response = await send_ipc_control_request(
        send_fn=orchestrator_client.send_stop,
        ipc_request=ipc_request,
    )

    return HTTPStopResponse(stopped=ipc_response.stopped)


@chat_router.post("/confirm")
async def confirm(
    body: HTTPConfirmRequest,
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
    orchestrator_client: OrchestratorClient = Depends(
        _get_orchestrator_client
    ),
) -> dict:
    """Process trust confirmation decision via Orchestrator IPC.

    Args:
        body: Confirmation request with action_id and decision.
        request: FastAPI request.
        claims: Authenticated token claims.
        orchestrator_client: Orchestrator IPC client.

    Returns:
        Confirmation result.
    """
    request_id = str(uuid.uuid4())
    ipc_request = IPCConfirmRequest(
        request_id=request_id,
        action_id=body.action_id,
        decision=body.decision,
    )
    ipc_response = await send_ipc_control_request(
        send_fn=orchestrator_client.send_confirm,
        ipc_request=ipc_request,
    )
    return build_confirm_response(
        accepted=ipc_response.accepted,
        action_id=body.action_id,
        status=ipc_response.status,
        message_id=ipc_response.message_id,
        result_summary=ipc_response.result_summary,
    )
