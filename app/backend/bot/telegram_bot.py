"""Telegram bot with aiogram 3.x long polling.

Minimal bridge-transport adapter. Registers a catch-all message handler
that silently ignores all direct messages. The bot exists only as a
polling endpoint; actual message processing flows through the chat
bridge via FastAPI intake routes.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

from backend.channels.telegram import (
    TelegramConfig,
    is_allowed_user,
)


logger = logging.getLogger(__name__)


@dataclass
class TelegramBot:
    """Telegram bot adapter for bridge transport.

    Runs aiogram long polling and silently ignores all direct messages.
    Message processing flows exclusively through the chat bridge.

    Attributes:
        config: Telegram adapter configuration.
    """

    config: TelegramConfig
    _bot: Bot = field(init=False, repr=False)
    _dispatcher: Dispatcher = field(init=False, repr=False)
    _polling_task: asyncio.Task | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        """Initialize bot and dispatcher."""
        self._bot = Bot(token=self.config.bot_token)
        self._dispatcher = Dispatcher()
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register message handlers."""
        self._dispatcher.message.register(self._handle_start, CommandStart())
        self._dispatcher.message.register(self._handle_message)

    def _is_allowed_telegram_user(self, user_id: int) -> bool:
        """Check whether one Telegram user may use this bot."""
        return is_allowed_user(user_id, self.config)

    async def _handle_start(self, message: types.Message) -> None:
        """Handle /start command with a brief greeting.

        Args:
            message: Incoming Telegram message.
        """
        if message.from_user is None:
            return
        if not self._is_allowed_telegram_user(message.from_user.id):
            return
        await message.answer(
            "biMEMO bot is active. "
            "Connect this bot to a web chat via the bridge settings."
        )

    async def _handle_message(self, message: types.Message) -> None:
        """Silently ignore all direct messages.

        All provider communication flows through the bridge.
        Direct DM to the bot is not supported.
        """

    async def start_polling(self) -> None:
        """Start long polling as a background task."""
        logger.info("Starting Telegram bot polling")
        self._polling_task = asyncio.create_task(
            self._dispatcher.start_polling(
                self._bot,
                handle_signals=False,
            )
        )

    async def stop_polling(self) -> None:
        """Stop the polling loop and close bot session."""
        logger.info("Stopping Telegram bot polling")
        await self._dispatcher.stop_polling()
        if self._polling_task is not None:
            self._polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._polling_task
            self._polling_task = None
        await self._bot.session.close()
