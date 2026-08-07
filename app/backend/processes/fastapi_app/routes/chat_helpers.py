"""Stateless helper functions for chat route handlers.

Provides response building, bridge synchronisation, conversation management,
and orchestrator communication helpers. All functions are pure or async helpers
that do not define FastAPI route endpoints. Used by the chat route module
to keep route handler bodies small and focused.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

import httpx
from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.ipc.ipc_channel import IPCChannelError
from backend.ipc.models import ChatRequest as IPCChatRequest
from backend.ipc.models import ChatResponse as IPCChatResponse
from backend.llm.structured_response import normalize_legacy_response_text
from backend.models.responses import ChatResponse as HTTPChatResponse
from backend.models.responses import ChatSettingsResponse, ConfirmationItem
from backend.models.responses import ChatStartResponse as HTTPChatStartResponse
from backend.models.responses import (
    ConversationDeleteResponse as HTTPConversationDeleteResponse,
)
from backend.models.responses import ConversationItem as HTTPConversationItem
from backend.models.responses import (
    ConversationMessageItem as HTTPConversationMessageItem,
)
from backend.models.responses import (
    StructuredAssistantContent as HTTPStructuredAssistantContent,
)

from .chat_file_helpers import prepare_chat_input_context
from .chat_models import PreparedChatRequest


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.channels.jwt_auth import TokenClaims
    from backend.chat_bridge import ChatBridgeService
    from backend.models.requests import ChatRequest as HTTPChatRequest

    from ..clients import AdminDBClient, OrchestratorClient
    from .chat_models import ChatInputContext


logger = logging.getLogger(__name__)

RECENT_CHAT_BRIDGE_SYNC_LIMIT = 6
DEFAULT_CONVERSATION_LIST_LIMIT = 50
DEFAULT_CONVERSATION_MESSAGES_LIMIT = 200

RECOVERABLE_BRIDGE_ERRORS = (RuntimeError, httpx.HTTPError)
MESSAGE_META_STRUCTURED_CONTENT_KEY = "structured_content"


def build_auto_bridge_title(message: str) -> str:
    """Build a stable bridge title for a new web-led conversation."""
    normalized = " ".join(message.split()).strip()
    if normalized:
        return normalized[:80]
    return "Web chat"


async def create_persisted_conversation(
    *,
    title: str,
    admin_client: AdminDBClient,
) -> HTTPConversationItem:
    """Create one persisted conversation without attaching any bridge."""
    conversation = await admin_client.create_conversation_item(title)
    if conversation is None:
        raise HTTPException(
            status_code=500,
            detail="Conversation was created without stored details",
        )
    return await conversation_item_with_bridge_provider(
        admin_client=admin_client,
        conversation=HTTPConversationItem(**conversation),
    )


def chat_settings_from_raw(raw: dict[str, str]) -> ChatSettingsResponse:
    """Convert raw key-value dict from DB into a typed settings response."""
    return ChatSettingsResponse(
        show_sql=raw.get("show_sql") == "1",
        show_thinking=raw.get("show_thinking") == "1",
        # Suggestions default ON when unset (unlike sql/thinking which default off).
        show_suggestions=raw.get("show_suggestions", "1") == "1",
    )


async def conversation_item_with_bridge_provider(
    *,
    admin_client: AdminDBClient,
    conversation: HTTPConversationItem,
) -> HTTPConversationItem:
    """Fill one conversation item with its active bridge provider and settings."""
    updates: dict[str, object] = {}
    bridge_link = await admin_client.get_chat_bridge_for_conversation(conversation.id)
    if bridge_link is not None and str(bridge_link.get("status", "")) == "active":
        updates["bridge_provider"] = str(bridge_link.get("provider", "")) or None
    raw_settings = await admin_client.list_chat_settings(conversation.id)
    if raw_settings:
        updates["settings"] = chat_settings_from_raw(raw_settings)
    if not updates:
        return conversation
    return conversation.model_copy(update=updates)


async def resolve_conversation_id(
    *,
    body: HTTPChatRequest,
    admin_client: AdminDBClient,
) -> str:
    """Return an existing conversation id or create a new persisted one."""
    conversation_id = body.conversation_id.strip()
    if conversation_id:
        return conversation_id
    conversation = await create_persisted_conversation(
        title="",
        admin_client=admin_client,
    )
    return conversation.id


async def sync_recent_bridge_messages(
    *,
    request: Request,
    chat_bridge_service: ChatBridgeService | None,
    conversation_id: str,
    created_by: str,
    bridge_title: str,
) -> None:
    """Sync recently saved conversation messages into the external bridge."""
    del request, created_by, bridge_title
    if chat_bridge_service is None:
        return
    try:
        await chat_bridge_service.sync_recent_conversation_messages(
            conversation_id=conversation_id,
            limit=RECENT_CHAT_BRIDGE_SYNC_LIMIT,
        )
    except RECOVERABLE_BRIDGE_ERRORS:
        logger.warning(
            "Failed to sync recent chat bridge messages for conversation %s",
            conversation_id,
            exc_info=True,
        )


async def prepare_chat_request(
    *,
    body: HTTPChatRequest,
    request: Request,
    claims: TokenClaims,
    admin_client: AdminDBClient,
    chat_bridge_service: ChatBridgeService | None,
) -> PreparedChatRequest:
    """Prepare the full orchestrator chat request from one web request."""
    chat_input_context = await prepare_chat_input_context(
        body=body,
        admin_client=admin_client,
    )
    conversation_id = await resolve_conversation_id(
        body=body,
        admin_client=admin_client,
    )
    ipc_request = build_ipc_chat_request(
        claims=claims,
        body=body,
        conversation_id=conversation_id,
        chat_input_context=chat_input_context,
    )
    return PreparedChatRequest(
        conversation_id=conversation_id,
        ipc_request=ipc_request,
    )


def build_ipc_chat_request(
    *,
    claims: TokenClaims,
    body: HTTPChatRequest,
    conversation_id: str,
    chat_input_context: ChatInputContext,
) -> IPCChatRequest:
    """Build one orchestrator IPC chat request from prepared web-chat input."""
    return IPCChatRequest(
        request_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        message=chat_input_context.message,
        channel="web",
        session_id=claims.jti,
        conversation_id=conversation_id,
        images=chat_input_context.images,
        attached_files=chat_input_context.resolved_files,
        voice=body.voice,
    )


async def send_chat_to_orchestrator(
    *,
    orchestrator_client: OrchestratorClient,
    ipc_request: IPCChatRequest,
    session_id: str,
) -> IPCChatResponse:
    """Send one prepared chat request to the orchestrator process."""
    try:
        return await orchestrator_client.send_chat(
            ipc_request,
            session_id=session_id,
        )
    except IPCChannelError as error:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator unavailable",
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            status_code=504,
            detail="Request timed out",
        ) from error


def build_confirmation_items(
    pending_confirmations: list[dict[str, object]] | None,
) -> list[ConfirmationItem]:
    """Convert raw pending confirmations into HTTP response items."""
    if not pending_confirmations:
        return []

    return [
        ConfirmationItem(
            action_id=confirmation_data.get("action_id", ""),
            tool_name=confirmation_data.get("tool_name", ""),
            parameters=confirmation_data.get("parameters", {}),
        )
        for confirmation_data in pending_confirmations
    ]


def build_http_chat_response(
    *,
    conversation_id: str,
    ipc_response: IPCChatResponse,
) -> HTTPChatResponse:
    """Build the final HTTP chat response from one IPC response."""
    structured_content = build_http_structured_content(ipc_response.structured_content)
    return HTTPChatResponse(
        conversation_id=conversation_id,
        input_filtered=ipc_response.input_filtered,
        output_filtered=ipc_response.output_filtered,
        pending_confirmations=build_confirmation_items(
            ipc_response.pending_confirmations,
        ),
        retrieved_memories=ipc_response.retrieved_memories,
        retrieved_messages=ipc_response.retrieved_messages,
        transcription=ipc_response.transcription,
        stats=ipc_response.stats,
        structured_content=structured_content,
        stream_enabled=ipc_response.stream_enabled,
    )


def parse_message_meta(
    raw_meta: str | None,
) -> dict[str, object]:
    """Parse one stored message metadata JSON string."""
    if not raw_meta:
        return {}
    try:
        parsed_meta = json.loads(raw_meta)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed_meta, dict):
        return {}
    return parsed_meta


def build_http_structured_content(
    structured_content: object,
    *,
    fallback_response_text: str = "",
) -> HTTPStructuredAssistantContent | None:
    """Convert one structured assistant object into the HTTP response shape."""
    if structured_content is None:
        response_text = fallback_response_text.strip()
        if not response_text:
            return None
        return HTTPStructuredAssistantContent(answer=response_text)
    if isinstance(structured_content, dict):
        response_value = structured_content.get(
            "answer",
            fallback_response_text,
        )
        thinking_value = structured_content.get("thinking")
        raw_sql = structured_content.get("sql")
        raw_suggestions = structured_content.get("suggestions", [])
    else:
        response_value = getattr(
            structured_content,
            "answer",
            fallback_response_text,
        )
        thinking_value = getattr(structured_content, "thinking", None)
        raw_sql = getattr(structured_content, "sql", None)
        raw_suggestions = getattr(structured_content, "suggestions", [])
    response_text = str(response_value).strip()
    thinking = (
        str(thinking_value).strip()
        if isinstance(thinking_value, str) and thinking_value.strip()
        else None
    )
    sql = str(raw_sql).strip() if isinstance(raw_sql, str) and raw_sql.strip() else None
    suggestions = (
        [
            str(suggestion).strip()
            for suggestion in raw_suggestions
            if str(suggestion).strip()
        ]
        if isinstance(raw_suggestions, list)
        else []
    )
    return HTTPStructuredAssistantContent(
        answer=response_text,
        thinking=thinking,
        sql=sql,
        suggestions=suggestions,
    )


def build_persisted_structured_content(
    *,
    role: str,
    content: str | list[dict[str, object]],
    raw_meta: str | None,
) -> HTTPStructuredAssistantContent | None:
    """Build structured content for one stored conversation message."""
    if role != "assistant" or not isinstance(content, str):
        return None

    message_meta = parse_message_meta(raw_meta)
    stored_content = message_meta.get(MESSAGE_META_STRUCTURED_CONTENT_KEY)
    if isinstance(stored_content, dict):
        return build_http_structured_content(
            stored_content,
            fallback_response_text=content,
        )

    normalized_content = normalize_legacy_response_text(content)
    return build_http_structured_content(normalized_content)


def build_conversation_message_item(
    message: dict[str, object],
) -> HTTPConversationMessageItem:
    """Convert one stored message row into the HTTP message shape."""
    content = message["content"]
    role = str(message["role"])
    raw_meta = str(message["meta"]) if message.get("meta") is not None else None
    structured_content = build_persisted_structured_content(
        role=role,
        content=content,
        raw_meta=raw_meta,
    )
    message_content = (
        structured_content.answer
        if structured_content is not None and isinstance(content, str)
        else content
    )
    return HTTPConversationMessageItem(
        id=str(message["id"]),
        role=role,
        content=message_content,
        conversation_id=str(message["conversation_id"]),
        created_at=str(message["created_at"]),
        meta=raw_meta,
        structured_content=structured_content,
    )


async def send_ipc_control_request(
    *,
    send_fn: Callable[[object], Awaitable[object]],
    ipc_request: object,
) -> object:
    """Send one stop/confirm IPC request and map transport errors to HTTP."""
    try:
        return await send_fn(ipc_request)
    except IPCChannelError as error:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator unavailable",
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            status_code=504,
            detail="Request timed out",
        ) from error


def build_confirm_response(
    *,
    accepted: bool,
    action_id: str,
    status: str = "",
    message_id: str = "",
    result_summary: str = "",
) -> dict[str, str]:
    """Build the HTTP response for one confirmation decision."""
    response = {"detail": confirm_detail(accepted), "action_id": action_id}
    if status:
        response["status"] = status
    if message_id:
        response["message_id"] = message_id
    if result_summary:
        response["result_summary"] = result_summary
    return response


def confirm_detail(accepted: bool) -> str:
    """Return the human-readable detail for one confirmation decision."""
    return "Approved" if accepted else "Denied"


def build_busy_chat_response() -> JSONResponse:
    """Build the standard response when the orchestrator is already busy."""
    return JSONResponse(
        {"detail": "Message queued", "status": 202},
        status_code=202,
    )


async def run_web_chat_request(
    *,
    body: HTTPChatRequest,
    request: Request,
    claims: TokenClaims,
    orchestrator_client: OrchestratorClient,
    admin_client: AdminDBClient,
    chat_bridge_service: ChatBridgeService | None,
) -> HTTPChatResponse:
    """Run one web chat request end to end and build the HTTP response."""
    prepared_request = await prepare_chat_request(
        body=body,
        request=request,
        claims=claims,
        admin_client=admin_client,
        chat_bridge_service=chat_bridge_service,
    )
    ipc_response = await send_chat_to_orchestrator(
        orchestrator_client=orchestrator_client,
        ipc_request=prepared_request.ipc_request,
        session_id=claims.jti,
    )
    await sync_recent_bridge_messages(
        request=request,
        chat_bridge_service=chat_bridge_service,
        conversation_id=prepared_request.conversation_id,
        created_by=claims.jti,
        bridge_title=build_auto_bridge_title(body.message),
    )
    return build_http_chat_response(
        conversation_id=prepared_request.conversation_id,
        ipc_response=ipc_response,
    )


async def _finish_started_web_chat_request(
    *,
    prepared_request: PreparedChatRequest,
    body: HTTPChatRequest,
    request: Request,
    claims: TokenClaims,
    orchestrator_client: OrchestratorClient,
    chat_bridge_service: ChatBridgeService | None,
) -> None:
    """Wait for a started chat turn and run post-completion integrations."""
    try:
        await send_chat_to_orchestrator(
            orchestrator_client=orchestrator_client,
            ipc_request=prepared_request.ipc_request,
            session_id=claims.jti,
        )
        await sync_recent_bridge_messages(
            request=request,
            chat_bridge_service=chat_bridge_service,
            conversation_id=prepared_request.conversation_id,
            created_by=claims.jti,
            bridge_title=build_auto_bridge_title(body.message),
        )
    except Exception:
        logger.exception("Background web chat request failed")


async def start_web_chat_request(
    *,
    body: HTTPChatRequest,
    request: Request,
    claims: TokenClaims,
    orchestrator_client: OrchestratorClient,
    admin_client: AdminDBClient,
    chat_bridge_service: ChatBridgeService | None,
    background_tasks: BackgroundTasks,
) -> HTTPChatStartResponse:
    """Start one web chat request and return before the final answer."""
    prepared_request = await prepare_chat_request(
        body=body,
        request=request,
        claims=claims,
        admin_client=admin_client,
        chat_bridge_service=chat_bridge_service,
    )
    orchestrator_client.register_chat_request(
        prepared_request.ipc_request.request_id,
        claims.jti,
        prepared_request.conversation_id,
    )
    background_tasks.add_task(
        _finish_started_web_chat_request,
        prepared_request=prepared_request,
        body=body,
        request=request,
        claims=claims,
        orchestrator_client=orchestrator_client,
        chat_bridge_service=chat_bridge_service,
    )
    return HTTPChatStartResponse(
        conversation_id=prepared_request.conversation_id,
        request_id=prepared_request.ipc_request.request_id,
        run_id=prepared_request.ipc_request.run_id,
    )


async def list_conversation_items(
    *,
    admin_client: AdminDBClient,
    limit: int,
    offset: int,
) -> list[HTTPConversationItem]:
    """Load and convert persisted conversations for the HTTP response."""
    conversations = await admin_client.list_conversations(
        limit=limit,
        offset=offset,
    )
    active_bridge_provider_by_conversation = {
        str(link["conversation_id"]): str(link["provider"])
        for link in await admin_client.list_chat_bridge_links()
        if str(link.get("status", "")) == "active"
    }
    all_chat_settings = await admin_client.list_all_chat_settings()
    return [
        HTTPConversationItem(
            **conversation,
            bridge_provider=active_bridge_provider_by_conversation.get(
                str(conversation["id"])
            ),
            settings=chat_settings_from_raw(
                all_chat_settings.get(str(conversation["id"]), {})
            ),
        )
        for conversation in conversations
    ]


async def list_conversation_message_items(
    *,
    admin_client: AdminDBClient,
    conversation_id: str,
    limit: int,
    offset: int,
) -> list[HTTPConversationMessageItem]:
    """Load, sort, and convert persisted messages for one conversation."""
    messages = await admin_client.list_messages(
        limit=limit,
        offset=offset,
        conversation_id=conversation_id,
    )
    ordered_messages = sorted(
        messages,
        key=lambda message: str(message.get("created_at", "")),
    )
    return [build_conversation_message_item(message) for message in ordered_messages]


async def update_conversation_item(
    *,
    admin_client: AdminDBClient,
    conversation_id: str,
    title: str,
) -> HTTPConversationItem:
    """Update one conversation title and convert the saved result."""
    conversation = await admin_client.update_conversation_title(
        conversation_id,
        title,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await conversation_item_with_bridge_provider(
        admin_client=admin_client,
        conversation=HTTPConversationItem(**conversation),
    )


async def delete_conversation_item(
    *,
    admin_client: AdminDBClient,
    conversation_id: str,
) -> HTTPConversationDeleteResponse:
    """Delete one conversation and build the HTTP success response."""
    await admin_client.delete_conversation(conversation_id)
    return HTTPConversationDeleteResponse(ok=True)
