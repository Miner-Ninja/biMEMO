"""Provider-facing routes for external chat bridge synchronization."""

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.chat_bridge.models import BridgeProvider
from backend.chat_bridge.providers import (
    REGISTERED_PROVIDERS,
    get_provider_class,
)
from backend.ipc.models import (
    BridgeInboundSyncRequest,
    StreamEventType,
    build_multimodal_content,
)
from backend.models.requests import ChatBridgeInboundMessageRequest
from backend.models.responses import ChatBridgeInboundMessageResponse

from ..clients import OrchestratorClient
from ..deps import (
    _get_audit_fn,
    _get_orchestrator_client,
    _require_chat_bridge_bearer,
)


chat_bridge_router = APIRouter(prefix="/bridge")

AuditCallable = Callable[..., Awaitable[None]]
AUDIT_EVENT_CHAT_BRIDGE_INBOUND = "chat_bridge_inbound_sync"
SUPPORTED_BRIDGE_INBOUND_PROVIDERS = frozenset(p.value for p in REGISTERED_PROVIDERS)


def _normalized_bridge_provider(provider: str) -> str:
    """Return one normalized bridge provider name."""
    return provider.strip().lower()


def _require_supported_bridge_inbound_provider(provider: str) -> str:
    """Return one supported inbound bridge provider or fail."""
    normalized_provider = _normalized_bridge_provider(provider)
    if normalized_provider in SUPPORTED_BRIDGE_INBOUND_PROVIDERS:
        return normalized_provider
    raise HTTPException(
        status_code=404,
        detail="Bridge provider not supported",
    )


def _validate_inbound_server(
    settings: object,
    provider: str,
    external_space_id: str,
) -> None:
    """Validate the inbound server through the provider or raise 403."""
    provider_class = get_provider_class(BridgeProvider(provider))
    if provider_class is None:
        return
    if not provider_class.validate_inbound_server(settings, external_space_id):
        raise HTTPException(
            status_code=403,
            detail=provider_class.server_mismatch_detail(),
        )


def _validate_inbound_user(
    settings: object,
    provider: str,
    author_id: str,
) -> None:
    """Validate the inbound author through the provider or raise 403."""
    provider_class = get_provider_class(BridgeProvider(provider))
    if provider_class is None:
        return
    allowed = provider_class.allowed_inbound_user_ids(settings)
    if allowed and author_id not in allowed:
        raise HTTPException(
            status_code=403,
            detail=provider_class.user_forbidden_detail(),
        )


def _provider_bridge_audit_tool_name(provider: str) -> str:
    """Return the audit tool name for one bridge provider."""
    return f"chat_bridge.{provider}"


def _bridge_inbound_event_data(
    *,
    conversation_id: str,
    message_id: str,
    provider: str,
    author_name: str,
    content: str,
    images: list[str],
    role: str = "user",
) -> dict[str, object]:
    """Build one browser event for a newly accepted inbound bridge message."""
    return {
        "conversation_id": conversation_id,
        "message": {
            "id": message_id,
            "role": role,
            "content": build_multimodal_content(content, images),
            "created_at": datetime.now(UTC).isoformat(),
            "source": provider,
            "author_name": author_name,
        },
    }


def _conversation_updated_event_data(
    *,
    conversation_id: str,
    updated_at: str,
) -> dict[str, object]:
    """Build one lightweight conversation update event."""
    return {
        "conversation_id": conversation_id,
        "updated_at": updated_at,
    }


@chat_bridge_router.post("/providers/{provider}/messages")
async def sync_bridge_inbound_message(
    provider: str,
    body: ChatBridgeInboundMessageRequest,
    request: Request,
    orchestrator: OrchestratorClient = Depends(  # noqa: B008
        _get_orchestrator_client
    ),
    audit_fn: AuditCallable = Depends(_get_audit_fn),  # noqa: B008
) -> ChatBridgeInboundMessageResponse:
    """Accept one inbound external bridge message for a linked web conversation."""
    normalized_provider = _require_supported_bridge_inbound_provider(provider)
    _require_chat_bridge_bearer(request, normalized_provider)
    settings = request.app.state.settings
    _validate_inbound_server(
        settings,
        normalized_provider,
        body.external_space_id,
    )
    _validate_inbound_user(
        settings,
        normalized_provider,
        body.author_id,
    )
    response = await orchestrator.send_bridge_inbound_sync(
        BridgeInboundSyncRequest(
            request_id=str(uuid.uuid4()),
            provider=normalized_provider,
            external_space_id=body.external_space_id,
            external_channel_id=body.external_channel_id,
            external_message_id=body.external_message_id,
            author_id=body.author_id,
            author_name=body.author_name,
            content=body.content,
            images=body.images,
        )
    )
    if response.reason == "internal_error":
        raise HTTPException(
            status_code=500,
            detail="Bridge inbound processing failed",
        )
    message_id = response.message_id
    if response.synced and response.conversation_id:
        synced_at = datetime.now(UTC).isoformat()
        await orchestrator.publish_conversation_event(
            conversation_id=response.conversation_id,
            event_type=StreamEventType.BRIDGE_INBOUND_MESSAGE,
            data=_bridge_inbound_event_data(
                conversation_id=response.conversation_id,
                message_id=message_id,
                provider=normalized_provider,
                author_name=body.author_name,
                content=body.content,
                images=body.images,
            ),
        )
        if response.assistant_content:
            await orchestrator.publish_conversation_event(
                conversation_id=response.conversation_id,
                event_type=StreamEventType.BRIDGE_INBOUND_MESSAGE,
                data=_bridge_inbound_event_data(
                    conversation_id=response.conversation_id,
                    message_id=response.assistant_message_id,
                    provider=normalized_provider,
                    author_name="",
                    content=response.assistant_content,
                    images=[],
                    role="assistant",
                ),
            )
        await orchestrator.publish_conversation_event(
            conversation_id=response.conversation_id,
            event_type=StreamEventType.CONVERSATION_UPDATED,
            data=_conversation_updated_event_data(
                conversation_id=response.conversation_id,
                updated_at=synced_at,
            ),
        )
    if response.synced:
        await audit_fn(
            event_type=AUDIT_EVENT_CHAT_BRIDGE_INBOUND,
            tool_name=_provider_bridge_audit_tool_name(normalized_provider),
            parameters=json.dumps(
                {
                    "provider": normalized_provider,
                    "external_channel_id": body.external_channel_id,
                    "external_message_id": body.external_message_id,
                    "author_id": body.author_id,
                }
            ),
            result=json.dumps(
                {
                    "message_id": message_id,
                    "synced": True,
                }
            ),
            zone="green",
        )
    return ChatBridgeInboundMessageResponse(
        message_id=message_id,
        synced=response.synced,
        reason=response.reason,
    )
