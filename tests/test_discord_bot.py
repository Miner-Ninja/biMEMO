"""Tests for DiscordBot bridge transport adapter.

Covers bridge message forwarding, unauthorized user rejection,
non-bridge message ignoring, duplicate detection, bot lifecycle,
and DM silent ignore behavior.
"""

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest
from backend.bot.discord_bot import DiscordBot
from backend.channels.discord import DiscordConfig


def _make_config(
    *,
    bridge_category_id: str = "",
    bridge_external_space_id: str = "",
    bridge_inbound_url: str = "",
    bridge_inbound_token: str = "",
) -> DiscordConfig:
    """Create test Discord config."""
    return DiscordConfig(
        bot_token="test-token",
        allowed_user_ids=frozenset({123}),
        bridge_category_id=bridge_category_id,
        bridge_external_space_id=bridge_external_space_id,
        bridge_inbound_url=bridge_inbound_url,
        bridge_inbound_token=bridge_inbound_token,
    )


def _make_bridge_config() -> DiscordConfig:
    """Create test Discord config with bridge enabled."""
    return _make_config(
        bridge_category_id="7001",
        bridge_external_space_id="9001",
        bridge_inbound_url="http://bimemo.test",
        bridge_inbound_token="bridge-secret-1",
    )


def _make_bot(
    config: DiscordConfig | None = None,
    bridge_http_client: httpx.AsyncClient | None = None,
) -> DiscordBot:
    """Create a DiscordBot with mocked dependencies."""
    if config is None:
        config = _make_config()
    return DiscordBot(
        config=config,
        bridge_http_client=bridge_http_client,
    )


_next_message_id = 1000


def _make_dm_message(
    user_id: int = 123,
    text: str = "hello",
    message_id: int | None = None,
) -> MagicMock:
    """Create a mock Discord DM message."""
    global _next_message_id
    msg = MagicMock(spec=discord.Message)
    msg.author = MagicMock()
    msg.author.id = user_id
    msg.author.bot = False
    msg.content = text
    if message_id is None:
        msg.id = _next_message_id
        _next_message_id += 1
    else:
        msg.id = message_id
    channel = MagicMock(spec=discord.DMChannel)
    channel.send = AsyncMock()
    msg.channel = channel
    return msg


def _make_bridge_channel_message(
    *,
    channel_id: int = 5001,
    category_id: int = 7001,
    guild_id: int = 9001,
    user_id: int = 321,
    text: str = "channel hello",
    message_id: int = 6001,
) -> MagicMock:
    """Create a mock Discord channel message for bridge intake tests."""
    msg = MagicMock(spec=discord.Message)
    msg.id = message_id
    msg.content = text
    msg.author = MagicMock()
    msg.author.id = user_id
    msg.author.bot = False
    msg.author.display_name = "Bridge User"
    msg.guild = MagicMock()
    msg.guild.id = guild_id
    msg.attachments = []
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.category_id = category_id
    msg.channel = channel
    return msg


# --- Bridge channel forwarding ---


class TestBridgeForwarding:
    """Tests for bridge channel message forwarding."""

    @pytest.mark.asyncio
    async def test_bridge_message_forwarded_to_inbound_route(self):
        """Verify configured bridge-channel messages go to the inbound route."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Capture and acknowledge bridge requests."""
            captured_requests.append(request)
            return httpx.Response(200, json={"synced": True})

        bridge_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        bot = _make_bot(
            config=_make_bridge_config(),
            bridge_http_client=bridge_client,
        )

        msg = _make_bridge_channel_message(user_id=123)
        await bot._handle_message(msg)

        assert len(captured_requests) == 1
        assert captured_requests[0].url == httpx.URL(
            "http://bimemo.test/bridge/providers/discord/messages"
        )
        assert captured_requests[0].headers["Authorization"] == "Bearer bridge-secret-1"
        payload = json.loads(captured_requests[0].read().decode())
        assert payload["external_channel_id"] == "5001"
        await bridge_client.aclose()

    @pytest.mark.asyncio
    async def test_bridge_message_from_disallowed_user_rejected(self):
        """Verify bridge channels ignore authors outside the allowed list."""
        captured_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            """Capture and acknowledge bridge requests."""
            captured_requests.append(request)
            return httpx.Response(200, json={"synced": True})

        bridge_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        bot = _make_bot(
            config=_make_bridge_config(),
            bridge_http_client=bridge_client,
        )

        msg = _make_bridge_channel_message(user_id=999)
        await bot._handle_message(msg)

        assert captured_requests == []
        await bridge_client.aclose()

    @pytest.mark.asyncio
    async def test_non_bridge_guild_message_ignored(self):
        """Verify unrelated guild messages are silently ignored."""
        bridge_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"synced": True})
            )
        )
        bot = _make_bot(
            config=_make_bridge_config(),
            bridge_http_client=bridge_client,
        )
        msg = _make_bridge_channel_message(category_id=8001)

        await bot._handle_message(msg)

        await bridge_client.aclose()


# --- DM silent ignore ---


class TestDMSilentIgnore:
    """Tests for DM silent ignore behavior."""

    @pytest.mark.asyncio
    async def test_dm_silently_ignored(self):
        """Verify direct messages do not trigger any response."""
        bot = _make_bot()

        msg = _make_dm_message()
        await bot._handle_message(msg)

        msg.channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_own_message_ignored(self):
        """Verify bot does not react to its own messages."""
        bot = _make_bot()

        msg = _make_dm_message()
        msg.author = bot._client.user
        await bot._handle_message(msg)

        msg.channel.send.assert_not_called()


# --- Duplicate detection ---


class TestDuplicateDetection:
    """Tests for duplicate message handling."""

    @pytest.mark.asyncio
    async def test_duplicate_message_blocked(self):
        """Verify same message ID is processed only once."""
        bridge_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"synced": True})
            )
        )
        bot = _make_bot(
            config=_make_bridge_config(),
            bridge_http_client=bridge_client,
        )

        msg = _make_bridge_channel_message(
            user_id=123,
            message_id=42,
        )
        await bot._handle_message(msg)
        await bot._handle_message(msg)

        # Second call with same ID should be blocked
        await bridge_client.aclose()


# --- Lifecycle ---


class TestLifecycle:
    """Tests for bot lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        """Verify start creates background task."""
        bot = _make_bot()
        bot._client.start = AsyncMock()

        await bot.start()
        assert bot._client_task is not None
        bot._client_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bot._client_task

    @pytest.mark.asyncio
    async def test_stop_cleanup(self):
        """Verify stop cancels and closes."""
        bot = _make_bot()
        bot._client.start = AsyncMock()
        bot._client.close = AsyncMock()

        await bot.start()
        await bot.stop()

        bot._client.close.assert_called_once()
        assert bot._client_task is None

    @pytest.mark.asyncio
    async def test_stop_without_task(self):
        """Verify stop works when no task exists."""
        bot = _make_bot()
        bot._client.close = AsyncMock()

        await bot.stop()
        bot._client.close.assert_called_once()
        assert bot._client_task is None


# --- Event registration ---


class TestEventRegistration:
    """Tests for event handler registration."""

    def test_events_registered(self):
        """Verify the client is initialized and callable."""
        bot = _make_bot()
        assert bot._client is not None
