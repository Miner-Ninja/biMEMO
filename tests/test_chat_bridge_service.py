"""Tests for provider-neutral chat bridge service and Discord provider."""

import asyncio
import json
import types

import httpx
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
from backend.chat_bridge.providers.discord import (
    MAX_MESSAGE_CONTENT_LENGTH,
    DiscordBridgeConfig,
    DiscordChatBridgeProvider,
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

    async def get_user_profile(self) -> dict[str, str]:
        """Return the fake stored user profile."""
        return dict(self.profile)

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


class TestDiscordProviderContract:
    """Tests for Discord provider contract methods used by bridge service."""

    def test_is_configured_all_present(self) -> None:
        """Return True when all three Discord settings are populated."""
        settings = types.SimpleNamespace(
            discord_bot_token="token-1",
            discord_bridge_category_id="cat-1",
            discord_bridge_server_id="guild-1",
        )
        assert DiscordChatBridgeProvider.is_configured(settings) is True

    def test_is_configured_missing_token(self) -> None:
        """Return False when bot token is empty."""
        settings = types.SimpleNamespace(
            discord_bot_token="",
            discord_bridge_category_id="cat-1",
            discord_bridge_server_id="guild-1",
        )
        assert DiscordChatBridgeProvider.is_configured(settings) is False

    def test_is_configured_missing_category(self) -> None:
        """Return False when category ID is empty."""
        settings = types.SimpleNamespace(
            discord_bot_token="token-1",
            discord_bridge_category_id="",
            discord_bridge_server_id="guild-1",
        )
        assert DiscordChatBridgeProvider.is_configured(settings) is False

    def test_is_configured_missing_server(self) -> None:
        """Return False when server ID is empty."""
        settings = types.SimpleNamespace(
            discord_bot_token="token-1",
            discord_bridge_category_id="cat-1",
            discord_bridge_server_id="",
        )
        assert DiscordChatBridgeProvider.is_configured(settings) is False

    def test_build_from_settings_creates_provider(self) -> None:
        """Construct a provider and verify its internal config matches settings."""
        settings = types.SimpleNamespace(
            discord_bot_token="token-1",
            discord_bridge_category_id="cat-1",
            discord_bridge_server_id="guild-1",
        )
        provider = DiscordChatBridgeProvider.build_from_settings(settings)
        assert isinstance(provider, DiscordChatBridgeProvider)
        assert provider._config.bot_token == "token-1"
        assert provider._config.category_id == "cat-1"
        assert provider._config.external_space_id == "guild-1"

    def test_validate_inbound_server_matching(self) -> None:
        """Return True when external space matches configured server."""
        settings = types.SimpleNamespace(discord_bridge_server_id="guild-1")
        assert (
            DiscordChatBridgeProvider.validate_inbound_server(settings, "guild-1")
            is True
        )

    def test_validate_inbound_server_mismatch(self) -> None:
        """Return False when external space does not match configured server."""
        settings = types.SimpleNamespace(discord_bridge_server_id="guild-1")
        assert (
            DiscordChatBridgeProvider.validate_inbound_server(settings, "guild-other")
            is False
        )

    def test_validate_inbound_server_empty_config(self) -> None:
        """Return True when no server is configured (open mode)."""
        settings = types.SimpleNamespace(discord_bridge_server_id="")
        assert (
            DiscordChatBridgeProvider.validate_inbound_server(settings, "any-guild")
            is True
        )

    def test_allowed_inbound_user_ids_parses_csv(self) -> None:
        """Parse comma-separated user IDs from settings."""
        settings = types.SimpleNamespace(
            discord_allowed_user_ids="user-1, user-2, user-3",
        )
        result = DiscordChatBridgeProvider.allowed_inbound_user_ids(settings)
        assert result == {"user-1", "user-2", "user-3"}

    def test_allowed_inbound_user_ids_empty(self) -> None:
        """Return empty set when no user IDs are configured."""
        settings = types.SimpleNamespace(discord_allowed_user_ids="")
        result = DiscordChatBridgeProvider.allowed_inbound_user_ids(settings)
        assert result == set()

    def test_default_persona_user_id_single(self) -> None:
        """Return the single ID when exactly one is allowed."""
        result = DiscordChatBridgeProvider.default_persona_user_id(
            frozenset({"user-1"}),
        )
        assert result == "user-1"

    def test_default_persona_user_id_multiple(self) -> None:
        """Return empty string when multiple IDs are allowed."""
        result = DiscordChatBridgeProvider.default_persona_user_id(
            frozenset({"user-1", "user-2"}),
        )
        assert result == ""

    def test_default_persona_user_id_empty(self) -> None:
        """Return empty string when no IDs are allowed."""
        result = DiscordChatBridgeProvider.default_persona_user_id(frozenset())
        assert result == ""

    def test_server_mismatch_detail(self) -> None:
        """Return Discord-specific server mismatch error string."""
        assert DiscordChatBridgeProvider.server_mismatch_detail() == (
            "Discord bridge server mismatch"
        )

    def test_user_forbidden_detail(self) -> None:
        """Return Discord-specific forbidden author error string."""
        assert DiscordChatBridgeProvider.user_forbidden_detail() == (
            "Discord author is not allowed for bridge sync"
        )


class TestDiscordChatBridgeProvider:
    """Tests for the Discord provider REST adapter."""

    @pytest.mark.asyncio
    async def test_create_bridge_target_posts_channel_creation_request(self) -> None:
        """Verify Discord provider creates a text channel through REST."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return one fake Discord response for basic channel creation."""
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={"id": "discord-channel-1"},
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        target = await provider.create_bridge_target(
            title="   Ops   bridge   room   for deployment  "
        )

        assert target == BridgeTarget(
            external_space_id="guild-1",
            external_channel_id="discord-channel-1",
        )
        assert captured_requests[0].url.path == "/guilds/guild-1/channels"
        assert captured_requests[0].headers["Authorization"] == "Bot token-1"
        payload = json.loads(captured_requests[0].content.decode())
        assert payload == {
            "name": "ops-bridge-room-for-deployment",
            "type": 0,
            "parent_id": "category-1",
        }
        await client.aclose()

    @pytest.mark.asyncio
    async def test_create_bridge_target_keeps_unicode_channel_title(self) -> None:
        """Verify Unicode titles do not collapse into the generic fallback."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return one fake Discord response for unicode channel creation."""
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={"id": "discord-channel-2"},
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        target = await provider.create_bridge_target(title="Test")

        assert target == BridgeTarget(
            external_space_id="guild-1",
            external_channel_id="discord-channel-2",
        )
        payload = json.loads(captured_requests[0].content.decode())
        assert payload == {
            "name": "test",
            "type": 0,
            "parent_id": "category-1",
        }
        await client.aclose()

    @pytest.mark.asyncio
    async def test_send_user_message_uses_webhook_persona(self) -> None:
        """Verify user messages go through a webhook with a custom username."""
        captured_requests: list[httpx.Request] = []
        persona_user_id = "test-discord-user-1"

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return fake Discord responses for persona webhook delivery."""
            captured_requests.append(request)
            if (
                request.method == "GET"
                and request.url.path == "/users/test-discord-user-1"
            ):
                return httpx.Response(
                    200,
                    json={
                        "id": persona_user_id,
                        "username": "minerninja",
                        "global_name": "ToxicNinja",
                        "avatar": "avatarhash123",
                    },
                )
            if (
                request.method == "GET"
                and request.url.path == "/channels/channel-1/webhooks"
            ):
                return httpx.Response(200, json=[])
            if (
                request.method == "POST"
                and request.url.path == "/channels/channel-1/webhooks"
            ):
                return httpx.Response(
                    200,
                    json={"id": "webhook-1", "token": "hook-token-1"},
                )
            if (
                request.method == "POST"
                and request.url.path == "/webhooks/webhook-1/hook-token-1"
            ):
                return httpx.Response(200, json={"id": "discord-message-9"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        message_ref = await provider.send_message(
            target=BridgeTarget(
                external_space_id="guild-1",
                external_channel_id="channel-1",
            ),
            message=OutboundBridgeMessage(
                role="user",
                content="Message from web",
                display_name="ToxicNinja",
                use_persona=True,
                persona_user_id=persona_user_id,
            ),
        )

        assert message_ref == ProviderMessageRef(
            external_message_id="discord-message-9"
        )
        request_paths = [request.url.path for request in captured_requests]
        assert "/channels/channel-1/webhooks" in request_paths
        assert "/users/test-discord-user-1" in request_paths
        webhook_execute_request = next(
            request
            for request in captured_requests
            if request.url.path == "/webhooks/webhook-1/hook-token-1"
        )
        assert webhook_execute_request.url.query == b"wait=true"
        assert "Authorization" not in webhook_execute_request.headers
        payload = json.loads(webhook_execute_request.content.decode())
        assert payload == {
            "content": "Message from web",
            "username": "ToxicNinja",
            "avatar_url": (
                "https://cdn.discordapp.com/avatars/"
                "test-discord-user-1/avatarhash123.png?size=256"
            ),
        }
        await client.aclose()

    @pytest.mark.asyncio
    async def test_send_user_message_splits_long_webhook_content(self) -> None:
        """Verify long persona messages are split into Discord-sized chunks."""
        captured_requests: list[httpx.Request] = []
        persona_user_id = "test-discord-user-1"
        long_content = ("alpha " * 450).strip()

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return fake Discord responses for split persona webhook delivery."""
            captured_requests.append(request)
            if (
                request.method == "GET"
                and request.url.path == "/users/test-discord-user-1"
            ):
                return httpx.Response(
                    200,
                    json={
                        "id": persona_user_id,
                        "username": "minerninja",
                        "global_name": "ToxicNinja",
                        "avatar": "avatarhash123",
                    },
                )
            if (
                request.method == "GET"
                and request.url.path == "/channels/channel-1/webhooks"
            ):
                return httpx.Response(200, json=[])
            if (
                request.method == "POST"
                and request.url.path == "/channels/channel-1/webhooks"
            ):
                return httpx.Response(
                    200,
                    json={"id": "webhook-1", "token": "hook-token-1"},
                )
            if (
                request.method == "POST"
                and request.url.path == "/webhooks/webhook-1/hook-token-1"
            ):
                message_index = sum(
                    1
                    for item in captured_requests
                    if item.method == "POST"
                    and item.url.path == "/webhooks/webhook-1/hook-token-1"
                )
                return httpx.Response(
                    200,
                    json={"id": f"discord-message-{message_index}"},
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        message_ref = await provider.send_message(
            target=BridgeTarget(
                external_space_id="guild-1",
                external_channel_id="channel-1",
            ),
            message=OutboundBridgeMessage(
                role="user",
                content=long_content,
                display_name="ToxicNinja",
                use_persona=True,
                persona_user_id=persona_user_id,
            ),
        )

        webhook_requests = [
            request
            for request in captured_requests
            if request.url.path == "/webhooks/webhook-1/hook-token-1"
        ]
        assert len(webhook_requests) == 2
        assert message_ref == ProviderMessageRef(
            external_message_id="discord-message-2"
        )
        for request in webhook_requests:
            payload = json.loads(request.content.decode())
            assert len(str(payload["content"])) <= MAX_MESSAGE_CONTENT_LENGTH
            assert payload["username"] == "ToxicNinja"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_send_user_message_fails_when_mapped_persona_lookup_breaks(
        self,
    ) -> None:
        """Fail when explicit mapped persona cannot be resolved from Discord."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return a fake Discord lookup failure for mapped persona delivery."""
            captured_requests.append(request)
            if request.method == "GET" and request.url.path == "/users/999":
                return httpx.Response(404, json={"message": "Unknown User"})
            if (
                request.method == "GET"
                and request.url.path == "/channels/channel-1/webhooks"
            ):
                return httpx.Response(200, json=[])
            if (
                request.method == "POST"
                and request.url.path == "/channels/channel-1/webhooks"
            ):
                return httpx.Response(
                    200,
                    json={"id": "webhook-1", "token": "hook-token-1"},
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await provider.send_message(
                target=BridgeTarget(
                    external_space_id="guild-1",
                    external_channel_id="channel-1",
                ),
                message=OutboundBridgeMessage(
                    role="user",
                    content="Message from web",
                    display_name="ToxicNinja",
                    use_persona=True,
                    persona_user_id="999",
                ),
            )

        request_paths = [request.url.path for request in captured_requests]
        assert "/users/999" in request_paths
        assert "/webhooks/webhook-1/hook-token-1" not in request_paths
        await client.aclose()

    @pytest.mark.asyncio
    async def test_send_assistant_message_uses_bot_identity(self) -> None:
        """Verify assistant messages still go through the bot account."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return one fake Discord bot response for assistant delivery."""
            captured_requests.append(request)
            return httpx.Response(200, json={"id": "discord-message-10"})

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        await provider.send_message(
            target=BridgeTarget(
                external_space_id="guild-1",
                external_channel_id="channel-1",
            ),
            message=OutboundBridgeMessage(
                role="assistant",
                content="Assistant reply",
                display_name="biMEMO",
            ),
        )

        assert captured_requests[0].url.path == "/channels/channel-1/messages"
        assert captured_requests[0].headers["Authorization"] == "Bot token-1"
        payload = json.loads(captured_requests[0].content.decode())
        assert payload == {"content": "Assistant reply"}
        await client.aclose()

    @pytest.mark.asyncio
    async def test_send_assistant_message_splits_long_bot_content(self) -> None:
        """Verify long assistant messages are split for Discord bot delivery."""
        captured_requests: list[httpx.Request] = []
        long_content = ("0123456789 " * 250).strip()

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return fake Discord bot responses for split assistant delivery."""
            captured_requests.append(request)
            message_index = sum(
                1
                for item in captured_requests
                if item.method == "POST"
                and item.url.path == "/channels/channel-1/messages"
            )
            return httpx.Response(
                200,
                json={"id": f"discord-message-{message_index}"},
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        message_ref = await provider.send_message(
            target=BridgeTarget(
                external_space_id="guild-1",
                external_channel_id="channel-1",
            ),
            message=OutboundBridgeMessage(
                role="assistant",
                content=long_content,
                display_name="biMEMO",
            ),
        )

        assert message_ref == ProviderMessageRef(
            external_message_id="discord-message-2"
        )
        bot_requests = [
            request
            for request in captured_requests
            if request.url.path == "/channels/channel-1/messages"
        ]
        assert len(bot_requests) == 2
        for request in bot_requests:
            payload = json.loads(request.content.decode())
            assert len(str(payload["content"])) <= MAX_MESSAGE_CONTENT_LENGTH
        await client.aclose()

    @pytest.mark.asyncio
    async def test_send_message_posts_content_to_discord_channel(self) -> None:
        """Verify Discord provider posts one message into the target channel."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return one fake Discord success response for a plain channel post."""
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={"id": "discord-message-1"},
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        message_ref = await provider.send_message(
            target=BridgeTarget(
                external_space_id="guild-1",
                external_channel_id="discord-channel-1",
            ),
            message=OutboundBridgeMessage(
                role="assistant",
                content="Bridge test message.",
                display_name="biMEMO",
            ),
        )

        assert message_ref == ProviderMessageRef(
            external_message_id="discord-message-1"
        )
        assert captured_requests[0].url.path == ("/channels/discord-channel-1/messages")
        assert json.loads(captured_requests[0].content.decode()) == {
            "content": "Bridge test message."
        }
        await client.aclose()

    @pytest.mark.asyncio
    async def test_send_user_message_raises_error_when_webhooks_forbidden(
        self,
    ) -> None:
        """Fail explicitly when persona delivery is forbidden by Discord."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return a fake Discord permission failure for webhook lookup."""
            captured_requests.append(request)
            if (
                request.method == "GET"
                and request.url.path == "/channels/channel-1/webhooks"
            ):
                return httpx.Response(
                    403,
                    json={"message": "Missing Permissions"},
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://discord.example.test",
        )
        provider = DiscordChatBridgeProvider(
            config=DiscordBridgeConfig(
                bot_token="token-1",
                category_id="category-1",
                external_space_id="guild-1",
                base_url="https://discord.example.test",
            ),
            client=client,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await provider.send_message(
                target=BridgeTarget(
                    external_space_id="guild-1",
                    external_channel_id="channel-1",
                ),
                message=OutboundBridgeMessage(
                    role="user",
                    content="Message from web",
                    display_name="ToxicNinja",
                    use_persona=True,
                ),
            )

        assert [request.url.path for request in captured_requests] == [
            "/channels/channel-1/webhooks"
        ]
        await client.aclose()
