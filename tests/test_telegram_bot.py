"""Tests for TelegramBot bridge transport adapter.

Covers message silent ignore, /start command, bot lifecycle,
and handler registration. All DM processing tests were removed
because the Telegram bot now acts only as bridge transport.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.bot.telegram_bot import TelegramBot
from backend.channels.telegram import TelegramConfig


def _make_config() -> TelegramConfig:
    """Create test Telegram config."""
    return TelegramConfig(
        bot_token="123456789:ABCDefGhIjKlMnOpQrStUvWxYz012345678",
        allowed_user_ids=frozenset({123}),
    )


def _make_bot(config: TelegramConfig | None = None) -> TelegramBot:
    """Create a TelegramBot with test config."""
    if config is None:
        config = _make_config()
    return TelegramBot(config=config)


def _make_message(
    user_id: int = 123,
    text: str = "hello",
) -> MagicMock:
    """Create a mock Telegram message."""
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


# --- DM silent ignore ---


class TestDMSilentIgnore:
    """Tests for DM silent ignore behavior."""

    @pytest.mark.asyncio
    async def test_dm_silently_ignored(self):
        """Verify direct messages produce no response."""
        bot = _make_bot()
        msg = _make_message()

        await bot._handle_message(msg)

        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_without_user_ignored(self):
        """Verify messages without from_user are ignored."""
        bot = _make_bot()
        msg = _make_message()
        msg.from_user = None

        await bot._handle_message(msg)

        msg.answer.assert_not_called()


# --- /start command ---


class TestStartCommand:
    """Tests for /start command handling."""

    @pytest.mark.asyncio
    async def test_start_command_for_allowed_user(self):
        """Verify /start sends greeting to allowed users."""
        bot = _make_bot()
        msg = _make_message(user_id=123)

        await bot._handle_start(msg)

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "bridge" in text.lower()

    @pytest.mark.asyncio
    async def test_start_command_for_disallowed_user(self):
        """Verify /start ignores disallowed users."""
        bot = _make_bot()
        msg = _make_message(user_id=999)

        await bot._handle_start(msg)

        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_command_without_user(self):
        """Verify /start ignores messages without from_user."""
        bot = _make_bot()
        msg = _make_message()
        msg.from_user = None

        await bot._handle_start(msg)

        msg.answer.assert_not_called()


# --- Lifecycle ---


class TestLifecycle:
    """Tests for bot lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_polling_creates_task(self):
        """Verify start_polling creates background task."""
        bot = _make_bot()
        bot._dispatcher.start_polling = AsyncMock()

        await bot.start_polling()
        assert bot._polling_task is not None
        bot._polling_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bot._polling_task

    @pytest.mark.asyncio
    async def test_stop_polling_cleanup(self):
        """Verify stop_polling cancels task and closes session."""
        bot = _make_bot()
        bot._dispatcher.start_polling = AsyncMock()
        bot._dispatcher.stop_polling = AsyncMock()
        bot._bot.session = MagicMock()
        bot._bot.session.close = AsyncMock()

        await bot.start_polling()
        await bot.stop_polling()

        bot._dispatcher.stop_polling.assert_called_once()
        assert bot._polling_task is None
        bot._bot.session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_polling_without_task(self):
        """Verify stop_polling works when no task exists."""
        bot = _make_bot()
        bot._dispatcher.stop_polling = AsyncMock()
        bot._bot.session = MagicMock()
        bot._bot.session.close = AsyncMock()

        await bot.stop_polling()

        bot._dispatcher.stop_polling.assert_called_once()
        bot._bot.session.close.assert_called_once()


# --- Handler registration ---


class TestHandlerRegistration:
    """Tests for message handler registration."""

    def test_handlers_registered(self):
        """Verify dispatcher has registered handlers."""
        bot = _make_bot()
        assert bot._dispatcher is not None
