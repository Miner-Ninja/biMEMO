"""Tests for provider-neutral chat bridge service and Discord provider."""

import asyncio
import json

import pytest

from backend.chat_bridge.models import (
    BridgeDeliveryMode,
    BridgeMessageDirection,
    BridgePersonaMode,
    BridgeProvider,
    BridgeStatus,
    BridgeSyncDirection,
    BridgeTarget,
    ChatBridgeMessageLink,
    ExternalBridgeMessage,
    OutboundBridgeMessage,
    ProviderMessageRef,
)
from backend.chat_bridge.service import (
    ChatBridgeService,
    ChatBridgeSyncSkippedError,
)


class FakeAdminClient:
    """Simple in-memory DB proxy stand-in for bridge service tests."""

    def __init__(self) -> None:
        """Initialize fake bridge storage."""
        self._links: dict[str, dict[str, object]] = {}
        self._links_by_target: dict[tuple[str, str, str], dict[str, object]] = {}
        self._message_links_by_external: dict[tuple[str, str], dict[str, object]] = {}
        self._message_links_by_message_id: dict[tuple[str, str], dict[str, object]] = {}
        self._message_links_by_finalization: dict[
            tuple[str, str], dict[str, object]
        ] = {}
        self.saved_messages: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []
        self.profile: dict[str, str] = {}

    async def send_operation(
        self,
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        include_error_data: bool = False,
    ) -> dict[str, object]:
        """Execute one fake bridge-related DB operation."""
        del include_error_data
        operation_parameters = parameters or {}
        if operation == "create_chat_bridge_link":
            link = {
                "id": "bridge-1",
                "conversation_id": str(operation_parameters["conversation_id"]),
                "provider": str(operation_parameters["provider"]),
                "external_space_id": str(operation_parameters["external_space_id"]),
                "external_channel_id": str(operation_parameters["external_channel_id"]),
                "created_by": str(operation_parameters["created_by"]),
                "sync_direction": BridgeSyncDirection.BIDIRECTIONAL.value,
                "status": BridgeStatus.ACTIVE.value,
                "delivery_mode": str(
                    operation_parameters.get("delivery_mode", "bot_only")
                ),
                "persona_mode": str(
                    operation_parameters.get("persona_mode", "neutral")
                ),
                "persona_user_id": str(operation_parameters.get("persona_user_id", "")),
                "created_at": "2026-03-25T10:00:00.000",
            }
            self._links[link["conversation_id"]] = link
            self._links_by_target[
                (
                    link["provider"],
                    link["external_space_id"],
                    link["external_channel_id"],
                )
            ] = link
            return {"chat_bridge_link": link}
        if operation == "get_chat_bridge_for_conversation":
            return {
                "chat_bridge_link": self._links.get(
                    str(operation_parameters["conversation_id"])
                )
            }
        if operation == "get_chat_bridge_link":
            for link in self._links.values():
                if link["id"] == str(operation_parameters["bridge_link_id"]):
                    return {"chat_bridge_link": link}
            return {"chat_bridge_link": None}
        if operation == "update_chat_bridge_link_settings":
            for conversation_id, link in self._links.items():
                if link["id"] != str(operation_parameters["bridge_link_id"]):
                    continue
                link["delivery_mode"] = str(operation_parameters["delivery_mode"])
                link["persona_mode"] = str(operation_parameters["persona_mode"])
                link["persona_user_id"] = str(operation_parameters["persona_user_id"])
                self._links[conversation_id] = link
                self._links_by_target[
                    (
                        str(link["provider"]),
                        str(link["external_space_id"]),
                        str(link["external_channel_id"]),
                    )
                ] = link
                return {"chat_bridge_link": link}
            return {"chat_bridge_link": None}
        if operation == "get_chat_bridge_by_external_target":
            key = (
                str(operation_parameters["provider"]),
                str(operation_parameters["external_space_id"]),
                str(operation_parameters["external_channel_id"]),
            )
            return {"chat_bridge_link": self._links_by_target.get(key)}
        if operation == "create_chat_bridge_message_link":
            link = {
                "id": (f"bridge-message-{len(self._message_links_by_external) + 1}"),
                "bridge_link_id": str(operation_parameters["bridge_link_id"]),
                "message_id": str(operation_parameters["message_id"]),
                "external_message_id": str(operation_parameters["external_message_id"]),
                "direction": str(operation_parameters["direction"]),
                "finalization_id": str(operation_parameters.get("finalization_id", "")),
                "created_at": "2026-03-25T10:01:00.000",
            }
            self._message_links_by_external[
                (link["bridge_link_id"], link["external_message_id"])
            ] = link
            self._message_links_by_message_id[
                (link["bridge_link_id"], link["message_id"])
            ] = link
            if link["finalization_id"]:
                self._message_links_by_finalization[
                    (link["bridge_link_id"], link["finalization_id"])
                ] = link
            return {"chat_bridge_message_link": link}
        if operation == "get_chat_bridge_message_link_by_external_message":
            key = (
                str(operation_parameters["bridge_link_id"]),
                str(operation_parameters["external_message_id"]),
            )
            return {
                "chat_bridge_message_link": self._message_links_by_external.get(key)
            }
        if operation == "get_chat_bridge_message_link_by_message_id":
            key = (
                str(operation_parameters["bridge_link_id"]),
                str(operation_parameters["message_id"]),
            )
            return {
                "chat_bridge_message_link": self._message_links_by_message_id.get(key)
            }
        if operation == "get_chat_bridge_message_link_by_finalization":
            key = (
                str(operation_parameters["bridge_link_id"]),
                str(operation_parameters["finalization_id"]),
            )
            return {
                "chat_bridge_message_link": self._message_links_by_finalization.get(key)
            }
        if operation == "claim_chat_bridge_finalization":
            key = (
                str(operation_parameters["bridge_link_id"]),
                str(operation_parameters["finalization_id"]),
            )
            existing = self._message_links_by_finalization.get(key)
            if existing is not None:
                return {"claimed": False, "chat_bridge_message_link": existing}
            link = {
                "id": f"bridge-message-{len(self._message_links_by_external) + 1}",
                "bridge_link_id": key[0],
                "message_id": str(operation_parameters["message_id"]),
                "external_message_id": f"pending:{key[1]}",
                "direction": str(operation_parameters["direction"]),
                "finalization_id": key[1],
                "created_at": "2026-03-25T10:01:00.000",
            }
            self._message_links_by_finalization[key] = link
            self._message_links_by_message_id[(key[0], link["message_id"])] = link
            return {"claimed": True, "chat_bridge_message_link": link}
        if operation == "complete_chat_bridge_finalization":
            key = (
                str(operation_parameters["bridge_link_id"]),
                str(operation_parameters["finalization_id"]),
            )
            link = self._message_links_by_finalization[key]
            link["external_message_id"] = str(
                operation_parameters["external_message_id"]
            )
            self._message_links_by_external[(key[0], link["external_message_id"])] = (
                link
            )
            return {"completed": True, "chat_bridge_message_link": link}
        if operation == "save_message":
            payload = dict(operation_parameters)
            self.saved_messages.append(payload)
            return {"message_id": "internal-message-1"}
        raise AssertionError(f"Unexpected operation: {operation}")

    async def get_user_profile(self) -> dict[str, object]:
        """Return the fake stored user profile, wrapped as the db-proxy handler does."""
        return {"profile": dict(self.profile)}

    async def update_chat_bridge_link_settings(
        self,
        *,
        bridge_link_id: str,
        delivery_mode: str,
        persona_mode: str,
        persona_user_id: str,
    ) -> dict[str, object] | None:
        """Update one stored bridge link through the direct admin-client helper."""
        result = await self.send_operation(
            "update_chat_bridge_link_settings",
            {
                "bridge_link_id": bridge_link_id,
                "delivery_mode": delivery_mode,
                "persona_mode": persona_mode,
                "persona_user_id": persona_user_id,
            },
        )
        return result.get("chat_bridge_link")

    async def list_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Return fake recent messages in admin-client format."""
        selected = [
            message
            for message in self.messages
            if conversation_id is None
            or str(message.get("conversation_id")) == conversation_id
        ]
        selected.sort(
            key=lambda message: str(message.get("created_at", "")),
            reverse=True,
        )
        return selected[offset : offset + limit]


class FakeProvider:
    """Provider stub for service tests."""

    def __init__(self) -> None:
        """Initialize captured provider calls."""
        self.created_titles: list[str] = []
        self.sent_messages: list[tuple[BridgeTarget, OutboundBridgeMessage]] = []

    async def create_bridge_target(self, *, title: str) -> BridgeTarget:
        """Return a stable fake bridge target."""
        self.created_titles.append(title)
        return BridgeTarget(
            external_space_id="guild-1",
            external_channel_id="channel-1",
        )

    async def send_message(
        self,
        *,
        target: BridgeTarget,
        message: OutboundBridgeMessage,
    ) -> ProviderMessageRef:
        """Record one outbound provider message."""
        self.sent_messages.append((target, message))
        return ProviderMessageRef(external_message_id="discord-message-1")


class TestChatBridgeService:
    """Tests for the provider-neutral bridge service."""

    @pytest.mark.asyncio
    async def test_create_bridge_persists_provider_target(self) -> None:
        """Verify bridge creation stores the provider-created target."""
        admin_client = FakeAdminClient()
        admin_client.profile = {"display_name": "ToxicNinja"}
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )

        bridge_link = await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
            title="Ops sync room",
        )

        assert bridge_link.conversation_id == "conversation-1"
        assert bridge_link.provider == BridgeProvider.DISCORD
        assert bridge_link.external_channel_id == "channel-1"
        assert bridge_link.delivery_mode == BridgeDeliveryMode.BOT_ONLY
        assert bridge_link.persona_mode == BridgePersonaMode.NEUTRAL
        assert provider.created_titles == ["Ops sync room"]

    @pytest.mark.asyncio
    async def test_create_bridge_rejects_unapproved_persona_user_id(self) -> None:
        """Verify mapped Discord personas are limited to the approved user list."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
            allowed_persona_user_ids=frozenset({"approved-user-1"}),
        )

        with pytest.raises(
            RuntimeError,
            match="persona_user_id is not allowed",
        ):
            await service.create_bridge(
                conversation_id="conversation-1",
                provider=BridgeProvider.DISCORD,
                created_by="admin-user",
                title="Ops sync room",
                delivery_mode=BridgeDeliveryMode.PERSONA_USER_AND_BOT_ASSISTANT.value,
                persona_mode=BridgePersonaMode.MAPPED_USER.value,
                persona_user_id="different-user-9",
            )

    @pytest.mark.asyncio
    async def test_create_bridge_defaults_to_one_allowed_discord_persona(self) -> None:
        """Verify one allowed Discord persona becomes the default send identity."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
            allowed_persona_user_ids=frozenset({"approved-user-1"}),
        )

        bridge_link = await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
            title="Ops sync room",
            delivery_mode=BridgeDeliveryMode.PERSONA_USER_AND_BOT_ASSISTANT.value,
        )

        assert bridge_link.delivery_mode == (
            BridgeDeliveryMode.PERSONA_USER_AND_BOT_ASSISTANT
        )
        assert bridge_link.persona_mode == BridgePersonaMode.MAPPED_USER
        assert bridge_link.persona_user_id == "approved-user-1"

    @pytest.mark.asyncio
    async def test_list_available_providers_returns_stable_provider_names(self) -> None:
        """Verify the service exposes configured providers for the web UI."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )

        assert service.list_available_providers() == [BridgeProvider.DISCORD]

    @pytest.mark.asyncio
    async def test_update_bridge_settings_stores_mapped_discord_user_id(self) -> None:
        """Verify existing Discord bridge settings can switch to one mapped user."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
            allowed_persona_user_ids=frozenset({"approved-user-1"}),
        )
        created_bridge = await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
            title="Ops sync room",
        )

        updated_bridge = await service.update_bridge_settings(
            bridge_link_id=created_bridge.id,
            delivery_mode=BridgeDeliveryMode.PERSONA_USER_AND_BOT_ASSISTANT.value,
            persona_mode=BridgePersonaMode.MAPPED_USER.value,
            persona_user_id="approved-user-1",
        )

        assert updated_bridge is not None
        assert updated_bridge.delivery_mode == (
            BridgeDeliveryMode.PERSONA_USER_AND_BOT_ASSISTANT
        )
        assert updated_bridge.persona_mode == BridgePersonaMode.MAPPED_USER
        assert updated_bridge.persona_user_id == "approved-user-1"

    @pytest.mark.asyncio
    async def test_sync_outbound_message_creates_provider_and_mapping_entry(
        self,
    ) -> None:
        """Verify outbound sync posts to provider and stores the mapping."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )

        message_link = await service.sync_outbound_message(
            conversation_id="conversation-1",
            message_id="web-message-1",
            role="assistant",
            content="Bridge check complete.",
        )

        assert isinstance(message_link, ChatBridgeMessageLink)
        assert message_link.direction == BridgeMessageDirection.OUTBOUND
        assert provider.sent_messages == [
            (
                BridgeTarget(
                    external_space_id="guild-1",
                    external_channel_id="channel-1",
                ),
                OutboundBridgeMessage(
                    role="assistant",
                    content="Bridge check complete.",
                    display_name="biMEMO",
                ),
            )
        ]

    @pytest.mark.asyncio
    async def test_sync_outbound_deduplicates_durable_finalization_id(self) -> None:
        """A replayed final outcome does not create a second bridge message."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )

        first = await service.sync_outbound_message(
            conversation_id="conversation-1",
            message_id="assistant-1",
            role="assistant",
            content="Done.",
            finalization_id="final-1",
        )
        replay = await service.sync_outbound_message(
            conversation_id="conversation-1",
            message_id="assistant-replay",
            role="assistant",
            content="Done.",
            finalization_id="final-1",
        )

        assert replay == first
        assert len(provider.sent_messages) == 1

    @pytest.mark.asyncio
    async def test_concurrent_finalization_delivery_claims_before_send(self) -> None:
        """Concurrent replay performs one external provider side effect."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )

        results = await asyncio.gather(
            service.sync_outbound_message(
                conversation_id="conversation-1",
                message_id="assistant-1",
                role="assistant",
                content="Done.",
                finalization_id="final-race",
            ),
            service.sync_outbound_message(
                conversation_id="conversation-1",
                message_id="assistant-2",
                role="assistant",
                content="Done.",
                finalization_id="final-race",
            ),
        )

        assert len(provider.sent_messages) == 1
        assert results[0].finalization_id == results[1].finalization_id == "final-race"

    @pytest.mark.asyncio
    async def test_unknown_claim_is_not_automatically_redispatched(self) -> None:
        """Crash-window state favors no duplicate external side effect."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        bridge = await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )
        await admin_client.send_operation(
            "claim_chat_bridge_finalization",
            {
                "bridge_link_id": bridge.id,
                "finalization_id": "final-unknown",
                "message_id": "assistant-1",
                "direction": "outbound",
            },
        )

        result = await service.sync_outbound_message(
            conversation_id="conversation-1",
            message_id="assistant-1",
            role="assistant",
            content="Done.",
            finalization_id="final-unknown",
        )

        assert result.external_message_id == "pending:final-unknown"
        assert provider.sent_messages == []

    @pytest.mark.asyncio
    async def test_sync_inbound_message_saves_internal_message_and_mapping(
        self,
    ) -> None:
        """Verify inbound provider messages enter the normal message store."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )

        saved_message_id = await service.sync_inbound_message(
            message=ExternalBridgeMessage(
                provider=BridgeProvider.DISCORD,
                external_space_id="guild-1",
                external_channel_id="channel-1",
                external_message_id="discord-message-200",
                author_id="discord-user-9",
                author_name="Ops Admin",
                content="Please check the latest deploy logs.",
            )
        )

        assert saved_message_id.conversation_id == "conversation-1"
        assert saved_message_id.message_id == "internal-message-1"
        assert admin_client.saved_messages[0]["conversation_id"] == "conversation-1"
        assert admin_client.saved_messages[0]["channel"] == "bridge:discord"
        meta = json.loads(str(admin_client.saved_messages[0]["meta"]))
        assert meta["source"] == "external_chat_bridge"
        assert meta["author_name"] == "Ops Admin"

    @pytest.mark.asyncio
    async def test_sync_inbound_message_ignores_duplicate_external_message(
        self,
    ) -> None:
        """Verify duplicate provider callbacks are reported as a controlled skip."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )
        first_message = ExternalBridgeMessage(
            provider=BridgeProvider.DISCORD,
            external_space_id="guild-1",
            external_channel_id="channel-1",
            external_message_id="discord-message-200",
            author_id="discord-user-9",
            author_name="Ops Admin",
            content="Please check the latest deploy logs.",
        )

        first_id = await service.sync_inbound_message(message=first_message)
        with pytest.raises(ChatBridgeSyncSkippedError) as error:
            await service.sync_inbound_message(message=first_message)

        assert first_id.conversation_id == "conversation-1"
        assert first_id.message_id == "internal-message-1"
        assert error.value.reason == "duplicate_message"
        assert len(admin_client.saved_messages) == 1

    @pytest.mark.asyncio
    async def test_same_text_from_web_and_provider_persists_two_rows(self) -> None:
        """Client-side idempotency never collapses a matching provider pair."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )

        await admin_client.send_operation(
            "save_message",
            {
                "conversation_id": "conversation-1",
                "role": "user",
                "content": "Please check the latest deploy logs.",
                "channel": "web",
                "client_message_id": "cm-web-1",
            },
        )
        saved_id = await service.sync_inbound_message(
            message=ExternalBridgeMessage(
                provider=BridgeProvider.DISCORD,
                external_space_id="guild-1",
                external_channel_id="channel-1",
                external_message_id="discord-message-200",
                author_id="discord-user-9",
                author_name="Ops Admin",
                content="Please check the latest deploy logs.",
            )
        )

        assert saved_id.message_id == "internal-message-1"
        assert len(admin_client.saved_messages) == 2
        web_saved = admin_client.saved_messages[0]
        assert web_saved["channel"] == "web"
        assert web_saved["client_message_id"] == "cm-web-1"
        bridge_saved = admin_client.saved_messages[1]
        assert bridge_saved["channel"] == "bridge:discord"
        assert bridge_saved.get("client_message_id") is None

    @pytest.mark.asyncio
    async def test_sync_inbound_message_runs_processor_and_syncs_reply(
        self,
    ) -> None:
        """Verify inbound bridge messages can trigger the normal chat flow."""
        admin_client = FakeAdminClient()
        provider = FakeProvider()

        async def inbound_processor(
            bridge_link: object,
            message: ExternalBridgeMessage,
        ) -> tuple[str, str, str]:
            """Simulate normal chat processing for one inbound bridge message."""
            admin_client.messages = [
                {
                    "id": "assistant-message-1",
                    "role": "assistant",
                    "content": "Processed reply from the model.",
                    "conversation_id": bridge_link.conversation_id,
                    "created_at": "2026-03-25T10:01:00.000",
                    "meta": "",
                },
                {
                    "id": "user-message-1",
                    "role": "user",
                    "content": message.content,
                    "conversation_id": bridge_link.conversation_id,
                    "created_at": "2026-03-25T10:00:00.000",
                    "meta": json.dumps(
                        {
                            "source": "external_chat_bridge",
                            "provider": "discord",
                        }
                    ),
                },
            ]
            return (
                "user-message-1",
                "Processed reply from the model.",
                "assistant-message-1",
            )

        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
            inbound_message_processor=inbound_processor,
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )

        saved_message_id = await service.sync_inbound_message(
            message=ExternalBridgeMessage(
                provider=BridgeProvider.DISCORD,
                external_space_id="guild-1",
                external_channel_id="channel-1",
                external_message_id="discord-message-201",
                author_id="discord-user-9",
                author_name="Ops Admin",
                content="Please answer in the bridge room.",
            )
        )

        assert saved_message_id.conversation_id == "conversation-1"
        assert saved_message_id.message_id == "user-message-1"
        assert provider.sent_messages == [
            (
                BridgeTarget(
                    external_space_id="guild-1",
                    external_channel_id="channel-1",
                ),
                OutboundBridgeMessage(
                    role="assistant",
                    content="Processed reply from the model.",
                    display_name="biMEMO",
                ),
            )
        ]

    @pytest.mark.asyncio
    async def test_sync_recent_conversation_messages_skips_already_synced_entries(
        self,
    ) -> None:
        """Verify recent outbound sync only sends messages without a mapping."""
        admin_client = FakeAdminClient()
        admin_client.profile = {"display_name": "ToxicNinja"}
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        bridge_link = await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )
        admin_client.messages = [
            {
                "id": "web-message-2",
                "role": "assistant",
                "content": "Assistant reply",
                "conversation_id": "conversation-1",
                "created_at": "2026-03-25T10:01:00.000",
                "meta": "",
            },
            {
                "id": "web-message-1",
                "role": "user",
                "content": "User message",
                "conversation_id": "conversation-1",
                "created_at": "2026-03-25T10:00:00.000",
                "meta": "",
            },
        ]
        await admin_client.send_operation(
            "create_chat_bridge_message_link",
            {
                "bridge_link_id": bridge_link.id,
                "message_id": "web-message-1",
                "external_message_id": "discord-message-1",
                "direction": "outbound",
            },
        )

        stored_links = await service.sync_recent_conversation_messages(
            conversation_id="conversation-1"
        )

        assert [link.message_id for link in stored_links] == ["web-message-2"]
        assert provider.sent_messages == [
            (
                BridgeTarget(
                    external_space_id="guild-1",
                    external_channel_id="channel-1",
                ),
                OutboundBridgeMessage(
                    role="assistant",
                    content="Assistant reply",
                    display_name="biMEMO",
                ),
            )
        ]

    @pytest.mark.asyncio
    async def test_sync_outbound_user_message_uses_profile_display_name(
        self,
    ) -> None:
        """Verify outbound web-user messages use the profile name for persona."""
        admin_client = FakeAdminClient()
        admin_client.profile = {"display_name": "ToxicNinja"}
        provider = FakeProvider()
        service = ChatBridgeService(
            admin_client=admin_client,
            providers={BridgeProvider.DISCORD: provider},
        )
        await service.create_bridge(
            conversation_id="conversation-1",
            provider=BridgeProvider.DISCORD,
            created_by="admin-user",
        )

        await service.sync_outbound_message(
            conversation_id="conversation-1",
            message_id="web-message-2",
            role="user",
            content="Which tools do you have available?",
        )

        assert provider.sent_messages == [
            (
                BridgeTarget(
                    external_space_id="guild-1",
                    external_channel_id="channel-1",
                ),
                OutboundBridgeMessage(
                    role="user",
                    content="Which tools do you have available?",
                    display_name="ToxicNinja",
                    use_persona=False,
                ),
            )
        ]


class TestRecentOutboundCandidate:
    """Tests for _recent_outbound_candidate static helper."""

    def test_plain_text_content_passes_through(self) -> None:
        """Plain string content is returned unchanged."""
        msg: dict[str, object] = {
            "id": "m1",
            "role": "assistant",
            "content": "hello",
        }
        result = ChatBridgeService._recent_outbound_candidate(msg)
        assert result is not None
        assert result["content"] == "hello"

    def test_multimodal_content_extracts_text_and_images(self) -> None:
        """Multimodal list content extracts text and image URLs separately."""
        data_url = "data:image/png;base64,abc"
        msg: dict[str, object] = {
            "id": "m2",
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
        result = ChatBridgeService._recent_outbound_candidate(msg)
        assert result is not None
        assert "base64" not in str(result["content"])
        assert "Describe this image" in str(result["content"])
        assert result["images"] == [data_url]

    def test_image_only_content_passes_with_empty_text(self) -> None:
        """Image-only multimodal content is eligible with images list."""
        data_url = "data:image/png;base64,abc"
        msg: dict[str, object] = {
            "id": "m3",
            "role": "assistant",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
        result = ChatBridgeService._recent_outbound_candidate(msg)
        assert result is not None
        assert result["images"] == [data_url]
