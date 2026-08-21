"""Tests for the Discord chat-bridge provider.

Covers the provider configuration contract (is_configured over the
Discord settings) and the REST adapter behavior: channel creation,
message sending, and the response/failure handling of the Discord
API calls.
"""

import json
import types

import httpx
import pytest

from backend.chat_bridge.models import (
    BridgeTarget,
    OutboundBridgeMessage,
    ProviderMessageRef,
)
from backend.chat_bridge.providers.discord import (
    MAX_MESSAGE_CONTENT_LENGTH,
    DiscordBridgeConfig,
    DiscordChatBridgeProvider,
)


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
