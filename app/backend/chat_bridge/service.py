"""Service layer for provider-neutral external chat bridge synchronization."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from backend.ipc.models import extract_image_urls, extract_text_content

from .models import (
    BridgeDeliveryMode,
    BridgeMessageDirection,
    BridgePersonaMode,
    BridgeProvider,
    BridgeSyncDirection,
    ChatBridgeLink,
    ChatBridgeMessageLink,
    ExternalBridgeMessage,
    OutboundBridgeMessage,
)
from .service_models import ChatBridgeSyncSkippedError, InboundBridgeSyncResult


if TYPE_CHECKING:
    from backend.processes.fastapi_app.clients import AdminDBClient

    from .provider import ChatBridgeProvider


SAVE_MESSAGE_ROLE = "user"
SAVE_MESSAGE_CHANNEL_PREFIX = "bridge:"
EXTERNAL_MESSAGE_META_SOURCE = "external_chat_bridge"
RECENT_OUTBOUND_SYNC_LIMIT = 6
OUTBOUND_SYNC_ROLES = {"user", "assistant"}
DEFAULT_WEB_DISPLAY_NAME = "Web User"
DEFAULT_ASSISTANT_DISPLAY_NAME = "biMEMO"
BRIDGE_SYNC_REASON_NOT_FOUND = "bridge_not_found"
BRIDGE_SYNC_REASON_DISABLED = "bridge_disabled"
BRIDGE_SYNC_REASON_DUPLICATE = "duplicate_message"
InboundMessageProcessor = Callable[
    [ChatBridgeLink, ExternalBridgeMessage],
    Awaitable[tuple[str, str, str]],
]


class ChatBridgeService:
    """Manage external chat bridges through the existing DB and API stack."""

    def __init__(
        self,
        *,
        admin_client: AdminDBClient,
        providers: dict[BridgeProvider, ChatBridgeProvider],
        inbound_message_processor: InboundMessageProcessor | None = None,
        allowed_persona_user_ids: frozenset[str] = frozenset(),
    ) -> None:
        """Initialize the bridge service with DB access and provider adapters."""
        self._admin_client = admin_client
        self._providers = providers
        self._inbound_message_processor = inbound_message_processor
        self._allowed_persona_user_ids = frozenset(
            user_id.strip() for user_id in allowed_persona_user_ids if user_id.strip()
        )

    @staticmethod
    def _normalized_persona_user_id(persona_user_id: str) -> str:
        """Return one trimmed persona user identifier."""
        return persona_user_id.strip()

    def _validated_persona_user_id(
        self,
        *,
        persona_mode: str,
        persona_user_id: str,
    ) -> str:
        """Return one validated persona user identifier for bridge creation."""
        normalized_persona_user_id = self._normalized_persona_user_id(persona_user_id)
        if (
            persona_mode == BridgePersonaMode.MAPPED_USER.value
            and not normalized_persona_user_id
        ):
            raise RuntimeError(
                "persona_user_id is required when persona_mode is mapped_user"
            )
        if (
            persona_mode == BridgePersonaMode.MAPPED_USER.value
            and normalized_persona_user_id not in self._allowed_persona_user_ids
        ):
            raise RuntimeError(
                "persona_user_id is not allowed for bridge persona mapping"
            )
        return normalized_persona_user_id

    def _provider_default_persona_user_id(
        self,
        provider: BridgeProvider,
    ) -> str:
        """Delegate default persona resolution to the provider."""
        from backend.chat_bridge.providers import get_provider_class

        provider_class = get_provider_class(provider)
        if provider_class is None:
            return ""
        return provider_class.default_persona_user_id(
            self._allowed_persona_user_ids,
        )

    def _resolved_bridge_persona_settings(
        self,
        *,
        provider: BridgeProvider,
        delivery_mode: str,
        persona_mode: str,
        persona_user_id: str,
    ) -> tuple[str, str]:
        """Return one validated bridge persona mode and provider user id."""
        default_persona = self._provider_default_persona_user_id(provider)
        if default_persona:
            if (
                delivery_mode == BridgeDeliveryMode.PERSONA_USER_AND_BOT_ASSISTANT.value
                and persona_mode == BridgePersonaMode.NEUTRAL.value
                and not persona_user_id.strip()
            ):
                return (
                    BridgePersonaMode.MAPPED_USER.value,
                    default_persona,
                )
            if (
                persona_mode == BridgePersonaMode.MAPPED_USER.value
                and not persona_user_id.strip()
            ):
                return persona_mode, default_persona
        return persona_mode, self._validated_persona_user_id(
            persona_mode=persona_mode,
            persona_user_id=persona_user_id,
        )

    @staticmethod
    def _outbound_persona_user_id(bridge_link: ChatBridgeLink) -> str:
        """Return the provider-side persona user id for one outbound user message."""
        if bridge_link.persona_mode != BridgePersonaMode.MAPPED_USER:
            return ""
        return bridge_link.persona_user_id

    @staticmethod
    def _should_use_user_persona_delivery(bridge_link: ChatBridgeLink) -> bool:
        """Return whether one user-authored message should use persona delivery."""
        return (
            bridge_link.delivery_mode
            == BridgeDeliveryMode.PERSONA_USER_AND_BOT_ASSISTANT
        )

    @staticmethod
    def _chat_bridge_link_from_payload(payload: object) -> ChatBridgeLink | None:
        """Convert one stored bridge-link payload into a model."""
        if not isinstance(payload, dict):
            return None
        return ChatBridgeLink.from_dict(payload)

    @staticmethod
    def _required_chat_bridge_link(payload: object) -> ChatBridgeLink:
        """Convert one bridge-link payload into a model or fail."""
        bridge_link = ChatBridgeService._chat_bridge_link_from_payload(payload)
        if bridge_link is None:
            raise RuntimeError("Bridge link creation did not return a stored link")
        return bridge_link

    @staticmethod
    def _chat_bridge_message_link_from_payload(
        payload: object,
    ) -> ChatBridgeMessageLink | None:
        """Convert one stored bridge-message payload into a model."""
        if not isinstance(payload, dict):
            return None
        return ChatBridgeMessageLink.from_dict(payload)

    @staticmethod
    def _required_chat_bridge_message_link(payload: object) -> ChatBridgeMessageLink:
        """Convert one stored bridge-message payload into a model or fail."""
        message_link = ChatBridgeService._chat_bridge_message_link_from_payload(payload)
        if message_link is None:
            raise RuntimeError(
                "Bridge message link creation did not return a stored mapping"
            )
        return message_link

    async def create_bridge(
        self,
        *,
        conversation_id: str,
        provider: BridgeProvider,
        created_by: str,
        title: str = "",
        sync_direction: str = BridgeSyncDirection.BIDIRECTIONAL.value,
        delivery_mode: str = BridgeDeliveryMode.BOT_ONLY.value,
        persona_mode: str = BridgePersonaMode.NEUTRAL.value,
        persona_user_id: str = "",
    ) -> ChatBridgeLink:
        """Create one provider-side bridge target and persist its link."""
        resolved_persona_mode, normalized_persona_user_id = (
            self._resolved_bridge_persona_settings(
                provider=provider,
                delivery_mode=delivery_mode,
                persona_mode=persona_mode,
                persona_user_id=persona_user_id,
            )
        )
        provider_adapter = self._get_provider(provider)
        target = await provider_adapter.create_bridge_target(
            title=title or conversation_id
        )
        result = await self._admin_client.send_operation(
            "create_chat_bridge_link",
            {
                "conversation_id": conversation_id,
                "provider": provider.value,
                "external_space_id": target.external_space_id,
                "external_channel_id": target.external_channel_id,
                "created_by": created_by,
                "sync_direction": sync_direction,
                "delivery_mode": delivery_mode,
                "persona_mode": resolved_persona_mode,
                "persona_user_id": normalized_persona_user_id,
            },
        )
        return self._required_chat_bridge_link(result.get("chat_bridge_link"))

    async def update_bridge_settings(
        self,
        *,
        bridge_link_id: str,
        delivery_mode: str,
        persona_mode: str,
        persona_user_id: str = "",
    ) -> ChatBridgeLink | None:
        """Update one stored bridge link presentation mode."""
        bridge_link = await self._bridge_link_from_operation(
            "get_chat_bridge_link",
            {"bridge_link_id": bridge_link_id},
        )
        if bridge_link is None:
            return None
        resolved_persona_mode, normalized_persona_user_id = (
            self._resolved_bridge_persona_settings(
                provider=bridge_link.provider,
                delivery_mode=delivery_mode,
                persona_mode=persona_mode,
                persona_user_id=persona_user_id,
            )
        )
        updated_bridge_link = await self._admin_client.update_chat_bridge_link_settings(
            bridge_link_id=bridge_link_id,
            delivery_mode=delivery_mode,
            persona_mode=resolved_persona_mode,
            persona_user_id=normalized_persona_user_id,
        )
        return self._chat_bridge_link_from_payload(updated_bridge_link)

    def list_available_providers(self) -> list[BridgeProvider]:
        """Return the configured external bridge providers in stable order."""
        return sorted(self._providers.keys(), key=lambda provider: provider.value)

    def default_persona_user_id_for_provider(self, provider: BridgeProvider) -> str:
        """Return the configured default provider user id for one bridge provider."""
        return self._provider_default_persona_user_id(provider)

    async def get_bridge_for_conversation(
        self,
        conversation_id: str,
    ) -> ChatBridgeLink | None:
        """Load one stored bridge link by web conversation identifier."""
        return await self._bridge_link_from_operation(
            "get_chat_bridge_for_conversation",
            {"conversation_id": conversation_id},
        )

    async def sync_outbound_message(
        self,
        *,
        conversation_id: str,
        message_id: str,
        role: str,
        content: str,
        images: list[str] | None = None,
    ) -> ChatBridgeMessageLink | None:
        """Send one web message into its linked external bridge target."""
        bridge_link = await self.get_bridge_for_conversation(conversation_id)
        if bridge_link is None or not bridge_link.allows_outbound_sync():
            return None
        provider_adapter = self._get_provider(bridge_link.provider)
        outbound_message = await self._build_outbound_message(
            bridge_link=bridge_link,
            role=role,
            content=content,
            images=images or [],
        )
        provider_message = await provider_adapter.send_message(
            target=bridge_link.target(),
            message=outbound_message,
        )
        return await self._save_message_link(
            bridge_link_id=bridge_link.id,
            message_id=message_id,
            external_message_id=provider_message.external_message_id,
            direction=BridgeMessageDirection.OUTBOUND,
        )

    async def sync_recent_conversation_messages(
        self,
        *,
        conversation_id: str,
        limit: int = RECENT_OUTBOUND_SYNC_LIMIT,
    ) -> list[ChatBridgeMessageLink]:
        """Send recent unsynchronized web messages into the linked bridge target."""
        bridge_link = await self.get_bridge_for_conversation(conversation_id)
        if bridge_link is None or not bridge_link.allows_outbound_sync():
            return []
        recent_messages = await self._admin_client.list_messages(
            limit=limit,
            offset=0,
            conversation_id=conversation_id,
        )
        stored_links: list[ChatBridgeMessageLink] = []
        for candidate in self._recent_outbound_candidates(recent_messages):
            stored_link = await self._sync_recent_message_candidate(
                conversation_id=conversation_id,
                bridge_link_id=bridge_link.id,
                candidate=candidate,
            )
            if stored_link is not None:
                stored_links.append(stored_link)
        return stored_links

    async def sync_inbound_message(
        self,
        *,
        message: ExternalBridgeMessage,
    ) -> InboundBridgeSyncResult:
        """Persist and process one external provider message inside the chat."""
        bridge_link = await self._get_bridge_by_target(
            provider=message.provider,
            external_space_id=message.external_space_id,
            external_channel_id=message.external_channel_id,
        )
        if bridge_link is None:
            raise ChatBridgeSyncSkippedError(BRIDGE_SYNC_REASON_NOT_FOUND)
        if not bridge_link.allows_inbound_sync():
            raise ChatBridgeSyncSkippedError(BRIDGE_SYNC_REASON_DISABLED)
        existing_link = await self._get_message_link_by_external_message(
            bridge_link_id=bridge_link.id,
            external_message_id=message.external_message_id,
        )
        if existing_link is not None:
            raise ChatBridgeSyncSkippedError(BRIDGE_SYNC_REASON_DUPLICATE)
        assistant_content = ""
        assistant_message_id = ""
        if self._inbound_message_processor is not None:
            (
                message_id,
                assistant_content,
                assistant_message_id,
            ) = await self._inbound_message_processor(bridge_link, message)
        else:
            message_id = await self._save_inbound_message_directly(message, bridge_link)
        await self._save_message_link(
            bridge_link_id=bridge_link.id,
            message_id=message_id,
            external_message_id=message.external_message_id,
            direction=BridgeMessageDirection.INBOUND,
        )
        if self._inbound_message_processor is not None:
            await self.sync_recent_conversation_messages(
                conversation_id=bridge_link.conversation_id,
                limit=RECENT_OUTBOUND_SYNC_LIMIT,
            )
        return InboundBridgeSyncResult(
            conversation_id=bridge_link.conversation_id,
            message_id=message_id,
            assistant_content=assistant_content,
            assistant_message_id=assistant_message_id,
        )

    async def _save_inbound_message_directly(
        self,
        message: ExternalBridgeMessage,
        bridge_link: ChatBridgeLink,
    ) -> str:
        """Store one inbound external message without running the LLM path."""
        save_result = await self._admin_client.send_operation(
            "save_message",
            {
                "conversation_id": bridge_link.conversation_id,
                "role": SAVE_MESSAGE_ROLE,
                "content": message.content,
                "channel": f"{SAVE_MESSAGE_CHANNEL_PREFIX}{message.provider.value}",
                "meta": self._inbound_message_meta(message),
            },
        )
        return str(save_result["message_id"])

    def _get_provider(self, provider: BridgeProvider) -> ChatBridgeProvider:
        """Return the configured provider adapter or fail fast."""
        provider_adapter = self._providers.get(provider)
        if provider_adapter is None:
            raise RuntimeError(f"No chat bridge provider configured for {provider}")
        return provider_adapter

    async def _bridge_link_from_operation(
        self,
        operation: str,
        params: dict[str, object],
    ) -> ChatBridgeLink | None:
        """Load one bridge link from one admin-client operation result."""
        result = await self._admin_client.send_operation(operation, params)
        return self._chat_bridge_link_from_payload(result.get("chat_bridge_link"))

    async def _message_link_from_operation(
        self,
        operation: str,
        params: dict[str, object],
    ) -> ChatBridgeMessageLink | None:
        """Load one bridge-message link from one admin-client operation result."""
        result = await self._admin_client.send_operation(operation, params)
        return self._chat_bridge_message_link_from_payload(
            result.get("chat_bridge_message_link")
        )

    @staticmethod
    def _recent_outbound_candidate(
        message: dict[str, object],
    ) -> dict[str, object] | None:
        """Return one recent message that is eligible for outbound sync."""
        message_id = str(message.get("id", "")).strip()
        role = str(message.get("role", "")).strip().lower()
        raw_content = message.get("content", "")
        content = extract_text_content(raw_content).strip()
        images = extract_image_urls(raw_content)
        if not message_id or role not in OUTBOUND_SYNC_ROLES:
            return None
        if not content and not images:
            return None
        if ChatBridgeService._is_external_bridge_message(message.get("meta")):
            return None
        return {
            "message_id": message_id,
            "role": role,
            "content": content,
            "images": images,
        }

    @classmethod
    def _recent_outbound_candidates(
        cls,
        recent_messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Return the recent web messages that are eligible for outbound sync."""
        candidates: list[dict[str, object]] = []
        for message in reversed(recent_messages):
            candidate = cls._recent_outbound_candidate(message)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def _should_sync_recent_message(
        self,
        *,
        bridge_link_id: str,
        message_id: str,
    ) -> bool:
        """Return whether one recent web message still needs outbound sync."""
        existing_link = await self._get_message_link_by_message_id(
            bridge_link_id=bridge_link_id,
            message_id=message_id,
        )
        return existing_link is None

    async def _sync_recent_message_candidate(
        self,
        *,
        conversation_id: str,
        bridge_link_id: str,
        candidate: dict[str, object],
    ) -> ChatBridgeMessageLink | None:
        """Send one eligible recent web message if it still has no bridge mapping."""
        message_id = str(candidate["message_id"])
        if not await self._should_sync_recent_message(
            bridge_link_id=bridge_link_id,
            message_id=message_id,
        ):
            return None
        images = candidate.get("images") or []
        return await self.sync_outbound_message(
            conversation_id=conversation_id,
            message_id=message_id,
            role=str(candidate["role"]),
            content=str(candidate.get("content", "")),
            images=list(images),
        )

    @staticmethod
    def _inbound_message_meta(message: ExternalBridgeMessage) -> str:
        """Build stored metadata for one inbound external message."""
        return json.dumps(
            {
                "source": EXTERNAL_MESSAGE_META_SOURCE,
                "provider": message.provider.value,
                "external_space_id": message.external_space_id,
                "external_channel_id": message.external_channel_id,
                "external_message_id": message.external_message_id,
                "author_id": message.author_id,
                "author_name": message.author_name,
            }
        )

    async def _user_outbound_message(
        self,
        *,
        bridge_link: ChatBridgeLink,
        content: str,
        images: list[str],
    ) -> OutboundBridgeMessage:
        """Build one outbound user message with profile and persona settings."""
        profile = await self._admin_client.get_user_profile()
        display_name = str(profile.get("display_name", "")).strip()
        avatar_url = str(profile.get("avatar_url", "")).strip()
        if not self._should_use_user_persona_delivery(bridge_link):
            return OutboundBridgeMessage(
                role="user",
                content=content,
                images=images,
                display_name=display_name or DEFAULT_WEB_DISPLAY_NAME,
                avatar_url=avatar_url,
                use_persona=False,
            )
        return OutboundBridgeMessage(
            role="user",
            content=content,
            images=images,
            display_name=display_name or DEFAULT_WEB_DISPLAY_NAME,
            avatar_url=avatar_url,
            use_persona=True,
            persona_user_id=self._outbound_persona_user_id(bridge_link),
        )

    @staticmethod
    def _assistant_outbound_message(
        normalized_role: str,
        content: str,
        images: list[str],
    ) -> OutboundBridgeMessage:
        """Build one outbound non-user message."""
        return OutboundBridgeMessage(
            role=normalized_role or "assistant",
            content=content,
            images=images,
            display_name=DEFAULT_ASSISTANT_DISPLAY_NAME,
        )

    async def _build_outbound_message(
        self,
        *,
        bridge_link: ChatBridgeLink,
        role: str,
        content: str,
        images: list[str],
    ) -> OutboundBridgeMessage:
        """Build one provider-neutral outbound bridge message payload."""
        normalized_role = role.strip().lower()
        if normalized_role == "user":
            return await self._user_outbound_message(
                bridge_link=bridge_link,
                content=content,
                images=images,
            )
        return self._assistant_outbound_message(
            normalized_role=normalized_role,
            content=content,
            images=images,
        )

    async def _get_bridge_by_target(
        self,
        *,
        provider: BridgeProvider,
        external_space_id: str,
        external_channel_id: str,
    ) -> ChatBridgeLink | None:
        """Load one stored bridge link by provider-side target identifiers."""
        return await self._bridge_link_from_operation(
            "get_chat_bridge_by_external_target",
            {
                "provider": provider.value,
                "external_space_id": external_space_id,
                "external_channel_id": external_channel_id,
            },
        )

    async def _get_message_link_by_external_message(
        self,
        *,
        bridge_link_id: str,
        external_message_id: str,
    ) -> ChatBridgeMessageLink | None:
        """Load one stored bridge message mapping by external message identifier."""
        return await self._message_link_from_operation(
            "get_chat_bridge_message_link_by_external_message",
            {
                "bridge_link_id": bridge_link_id,
                "external_message_id": external_message_id,
            },
        )

    async def _get_message_link_by_message_id(
        self,
        *,
        bridge_link_id: str,
        message_id: str,
    ) -> ChatBridgeMessageLink | None:
        """Load one stored bridge message mapping by internal message identifier."""
        return await self._message_link_from_operation(
            "get_chat_bridge_message_link_by_message_id",
            {
                "bridge_link_id": bridge_link_id,
                "message_id": message_id,
            },
        )

    async def _save_message_link(
        self,
        *,
        bridge_link_id: str,
        message_id: str,
        external_message_id: str,
        direction: BridgeMessageDirection,
    ) -> ChatBridgeMessageLink:
        """Persist one bridge message mapping and return the stored mapping."""
        result = await self._admin_client.send_operation(
            "create_chat_bridge_message_link",
            {
                "bridge_link_id": bridge_link_id,
                "message_id": message_id,
                "external_message_id": external_message_id,
                "direction": direction.value,
            },
        )
        return self._required_chat_bridge_message_link(
            result.get("chat_bridge_message_link")
        )

    @staticmethod
    def _is_external_bridge_message(meta: object) -> bool:
        """Return whether one stored message came from an inbound bridge sync."""
        if not isinstance(meta, str) or not meta.strip():
            return False
        try:
            payload = json.loads(meta)
        except json.JSONDecodeError:
            return False
        return payload.get("source") == EXTERNAL_MESSAGE_META_SOURCE
