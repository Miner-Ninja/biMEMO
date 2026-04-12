"""Bridge message handling for external chat integrations.

Processes inbound messages from external bridge providers (Discord, Telegram)
and routes them through the orchestrator chat path. Builds bridge services,
message metadata, and capture-save wrappers for tracking persisted message IDs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.chat_bridge import (
    BridgeProvider,
    ChatBridgeService,
    ExternalBridgeMessage,
    InboundBridgeSyncResult,
)
from backend.chat_bridge.providers import (
    build_configured_providers,
)

from .chat_handler import (
    DEFAULT_CONVERSATION_PROCESSING_STAGE,
    _run_orchestrator_message,
    _run_with_processing_state,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.config import Settings
    from backend.ipc.models import BridgeInboundSyncRequest

    from .client import DBProxyClient


logger = logging.getLogger(__name__)

BRIDGE_MESSAGE_CHANNEL_PREFIX = "bridge:"
BRIDGE_MESSAGE_META_SOURCE = "external_chat_bridge"
INTERNAL_FASTAPI_HOST = "127.0.0.1"


def _build_external_bridge_message(
    request: BridgeInboundSyncRequest,
) -> ExternalBridgeMessage:
    """Convert one inbound bridge IPC request into the bridge message shape."""
    return ExternalBridgeMessage(
        provider=BridgeProvider(request.provider),
        external_space_id=request.external_space_id,
        external_channel_id=request.external_channel_id,
        external_message_id=request.external_message_id,
        author_id=request.author_id,
        author_name=request.author_name,
        content=request.content,
        images=request.images,
    )


def _build_bridge_message_meta(
    message: ExternalBridgeMessage,
) -> dict[str, str]:
    """Build stored metadata for one inbound external bridge message."""
    return {
        "source": BRIDGE_MESSAGE_META_SOURCE,
        "provider": message.provider.value,
        "external_space_id": message.external_space_id,
        "external_channel_id": message.external_channel_id,
        "external_message_id": message.external_message_id,
        "author_id": message.author_id,
        "author_name": message.author_name,
    }


def _build_bridge_capture_save_fn(
    *,
    original_save_fn: Callable,
    message_source: str,
    captured_ids: dict[str, str | None],
) -> Callable:
    """Build a save wrapper that captures stored user and assistant message ids."""

    async def capture_save_fn(
        conversation_id: str,
        role: str,
        content: str,
        meta: dict | None = None,
    ) -> str:
        """Save one bridge message and capture user/assistant ids."""
        saved_message_id = await original_save_fn(
            conversation_id,
            role,
            content,
            meta=meta,
        )
        if (
            role == "user"
            and captured_ids["user"] is None
            and isinstance(meta, dict)
            and meta.get("source") == message_source
        ):
            captured_ids["user"] = saved_message_id
        elif role == "assistant" and captured_ids["assistant"] is None:
            captured_ids["assistant"] = saved_message_id
        return saved_message_id

    return capture_save_fn


def _resolve_bridge_link_context(
    bridge_link: object,
) -> tuple[str, str]:
    """Extract the conversation and session identifiers from one bridge link."""
    return (
        str(getattr(bridge_link, "conversation_id", "")),
        str(getattr(bridge_link, "created_by", "")),
    )


async def _run_bridge_message_through_orchestrator(
    *,
    orch: object,
    llm_call: Callable,
    message: ExternalBridgeMessage,
    session_id: str,
    conversation_id: str,
    message_meta: dict[str, str],
) -> str:
    """Run one inbound bridge message through the normal chat path."""
    response = await _run_orchestrator_message(
        orch=orch,
        message=message.content,
        channel=f"{BRIDGE_MESSAGE_CHANNEL_PREFIX}{message.provider.value}",
        llm_call=llm_call,
        session_id=session_id,
        conversation_id=conversation_id,
        images=message.images or None,
        meta=message_meta,
    )
    return response.structured_content.answer


async def _process_bridge_inbound_message(
    *,
    bridge_link: object,
    message: ExternalBridgeMessage,
    orch: object,
    db_client: DBProxyClient,
    llm_call: Callable | None,
) -> tuple[str, str, str]:
    """Run one inbound external bridge message through the normal chat loop.

    Returns:
        Tuple of (user_message_id, assistant_text, assistant_message_id).
    """
    if llm_call is None:
        raise RuntimeError("No LLM provider configured for bridge inbound sync")

    original_save_fn = orch.save_fn
    captured_ids: dict[str, str | None] = {
        "user": None,
        "assistant": None,
    }
    message_meta = _build_bridge_message_meta(message)
    conversation_id, session_id = _resolve_bridge_link_context(bridge_link)

    try:
        orch.save_fn = _build_bridge_capture_save_fn(
            original_save_fn=original_save_fn,
            message_source=BRIDGE_MESSAGE_META_SOURCE,
            captured_ids=captured_ids,
        )
        assistant_text = await _run_with_processing_state(
            db_client=db_client,
            conversation_id=conversation_id,
            processing_stage=DEFAULT_CONVERSATION_PROCESSING_STAGE,
            runner=lambda: _run_bridge_message_through_orchestrator(
                orch=orch,
                llm_call=llm_call,
                message=message,
                session_id=session_id,
                conversation_id=conversation_id,
                message_meta=message_meta,
            ),
        )
    finally:
        orch.save_fn = original_save_fn

    if not captured_ids["user"]:
        raise RuntimeError("Bridge inbound sync did not persist the user message")
    return (
        captured_ids["user"],
        assistant_text or "",
        captured_ids["assistant"] or "",
    )


def _build_bridge_providers(
    settings: Settings,
) -> dict[BridgeProvider, object]:
    """Build the configured external bridge providers."""
    return build_configured_providers(settings)


def _collect_persona_user_ids(settings: Settings) -> frozenset[str]:
    """Collect allowed persona user IDs from all configured providers."""
    from backend.chat_bridge.providers import REGISTERED_PROVIDERS

    all_ids: set[str] = set()
    for provider_class in REGISTERED_PROVIDERS.values():
        all_ids.update(
            provider_class.allowed_inbound_user_ids(settings),
        )
    return frozenset(all_ids)


def _build_bridge_service(
    *,
    db_client: DBProxyClient,
    settings: Settings,
    inbound_message_processor: Callable[
        [object, ExternalBridgeMessage],
        Awaitable[tuple[str, str, str]],
    ],
) -> ChatBridgeService:
    """Build the chat bridge service used by the orchestrator process."""
    bridge_providers = _build_bridge_providers(settings)
    allowed_persona_user_ids = _collect_persona_user_ids(settings)
    return ChatBridgeService(
        admin_client=db_client,
        providers=bridge_providers,
        inbound_message_processor=inbound_message_processor,
        allowed_persona_user_ids=allowed_persona_user_ids,
    )


def _build_bridge_message_processor(
    *,
    orch: object,
    db_client: DBProxyClient,
    get_llm_call: Callable[[], Callable | None],
) -> Callable[[object, ExternalBridgeMessage], Awaitable[tuple[str, str, str]]]:
    """Build the inbound bridge message processor used by the bridge service."""

    async def process_inbound_bridge_message(
        bridge_link: object,
        message: ExternalBridgeMessage,
    ) -> tuple[str, str, str]:
        """Process one inbound bridge message through the chat orchestrator."""
        return await _process_bridge_inbound_message(
            bridge_link=bridge_link,
            message=message,
            orch=orch,
            db_client=db_client,
            llm_call=get_llm_call(),
        )

    return process_inbound_bridge_message


def _build_bridge_inbound_handler(
    *,
    bridge_service: ChatBridgeService,
) -> Callable[[BridgeInboundSyncRequest], Awaitable[InboundBridgeSyncResult]]:
    """Build the handler that accepts one inbound bridge sync request."""

    async def bridge_inbound_fn(
        request: BridgeInboundSyncRequest,
    ) -> InboundBridgeSyncResult:
        """Sync one external bridge message into the internal conversation."""
        return await bridge_service.sync_inbound_message(
            message=_build_external_bridge_message(request)
        )

    return bridge_inbound_fn


def _build_internal_bridge_inbound_url(settings: Settings) -> str:
    """Build the internal FastAPI URL used by messenger bridge intake."""
    return f"http://{INTERNAL_FASTAPI_HOST}:{settings.port}"
