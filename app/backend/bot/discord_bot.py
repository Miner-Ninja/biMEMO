"""Discord bot as bridge transport adapter.

Runs a discord.py Client that listens for messages in bridge-linked
channels and forwards them to the internal FastAPI bridge intake route.
Direct messages are silently ignored — all LLM communication flows
exclusively through the chat bridge.
"""

import asyncio
import base64
import contextlib
import logging
from dataclasses import dataclass, field

import discord
import httpx

from backend.channels.discord import (
    DEFAULT_BRIDGE_INBOUND_TIMEOUT_SECONDS,
    DiscordAdapter,
    DiscordConfig,
    is_allowed_user,
)


logger = logging.getLogger(__name__)

BRIDGE_ROUTE_PATH = "/bridge/providers/discord/messages"
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_INLINE_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
RECOVERABLE_ATTACHMENT_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    discord.HTTPException,
)


@dataclass
class DiscordBot:
    """Discord bot adapter for bridge transport.

    Listens for messages in bridge-linked channels and forwards them
    to the FastAPI bridge intake route. Direct messages are ignored.

    Attributes:
        config: Discord adapter configuration.
        bridge_http_client: Optional pre-built HTTP client for bridge intake.
    """

    config: DiscordConfig
    bridge_http_client: httpx.AsyncClient | None = None
    _client: discord.Client = field(init=False, repr=False)
    _adapter: DiscordAdapter = field(init=False, repr=False)
    _bridge_http_client: httpx.AsyncClient | None = field(
        init=False,
        repr=False,
        default=None,
    )
    _owns_bridge_http_client: bool = field(
        init=False,
        repr=False,
        default=False,
    )
    _client_task: asyncio.Task | None = field(
        init=False,
        repr=False,
        default=None,
    )

    def __post_init__(self) -> None:
        """Initialize client, adapter, and register events."""
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._adapter = DiscordAdapter(config=self.config)
        if self.bridge_http_client is not None:
            self._bridge_http_client = self.bridge_http_client
        elif self._has_bridge_intake_config():
            self._bridge_http_client = httpx.AsyncClient(
                timeout=DEFAULT_BRIDGE_INBOUND_TIMEOUT_SECONDS,
            )
            self._owns_bridge_http_client = True
        self._register_events()

    def _register_events(self) -> None:
        """Register Discord event handlers."""

        @self._client.event
        async def on_ready() -> None:
            """Log successful connection."""
            logger.info(
                "Discord bot connected as %s",
                self._client.user,
            )

        @self._client.event
        async def on_message(message: discord.Message) -> None:
            """Route incoming messages to bridge handler."""
            await self._handle_message(message)

    # --- Message routing ---

    async def _handle_message(self, message: discord.Message) -> None:
        """Handle incoming Discord messages.

        Forwards bridge-channel messages to the intake route.
        All other messages (including DMs) are silently ignored.

        Args:
            message: Incoming Discord message.
        """
        if self._should_ignore_message(message):
            return
        await self._try_handle_bridge_channel_message(message)

    def _should_ignore_message(self, message: discord.Message) -> bool:
        """Return whether one Discord message should be ignored immediately."""
        if message.author == self._client.user:
            return True
        if getattr(message.author, "bot", False) is True:
            return True
        if not self._adapter.is_duplicate_message(message.id):
            return False
        logger.warning(
            "Duplicate message %s from Discord, skipping",
            message.id,
        )
        return True

    # --- Bridge channel forwarding ---

    async def _try_handle_bridge_channel_message(
        self,
        message: discord.Message,
    ) -> bool:
        """Forward one bridge-channel message into the FastAPI bridge route."""
        if not self._is_bridge_channel_message(message):
            return False
        payload = await self._prepare_bridge_inbound_payload(message)
        if payload is None:
            return True
        await self._post_bridge_inbound_payload(
            message=message,
            payload=payload,
        )
        return True

    async def _prepare_bridge_inbound_payload(
        self,
        message: discord.Message,
    ) -> dict[str, object] | None:
        """Return one validated bridge payload or None when the message stops here."""
        if not self._can_process_bridge_channel_message(message):
            return None
        payload = await self._build_bridge_inbound_payload(message)
        has_content = bool(payload["content"])
        has_images = bool(payload.get("images"))
        if not has_content and not has_images:
            return None
        return payload

    async def _build_bridge_inbound_payload(
        self,
        message: discord.Message,
    ) -> dict[str, object]:
        """Build the payload sent into the internal bridge intake route."""
        images = await self._extract_inline_images(message)
        return {
            "external_space_id": self.config.bridge_external_space_id,
            "external_channel_id": str(message.channel.id),
            "external_message_id": str(message.id),
            "author_id": str(message.author.id),
            "author_name": str(message.author.display_name),
            "content": (message.content or "").strip(),
            "images": images,
        }

    def _can_process_bridge_channel_message(
        self,
        message: discord.Message,
    ) -> bool:
        """Return whether one bridge message is allowed to enter FastAPI intake."""
        if not self._is_bridge_channel_message(message):
            return False
        if self._bridge_http_client is None:
            logger.warning(
                "Bridge channel message %s arrived without bridge HTTP client",
                message.id,
            )
            return False
        if not is_allowed_user(message.author.id, self.config):
            logger.warning(
                "Rejected Discord bridge message %s from unauthorized user %s",
                message.id,
                message.author.id,
            )
            return False
        return True

    async def _post_bridge_inbound_payload(
        self,
        *,
        message: discord.Message,
        payload: dict[str, str],
    ) -> None:
        """Post one prepared bridge payload into the internal FastAPI bridge route."""
        response = await self._bridge_http_client.post(
            self._build_bridge_inbound_url(),
            json=payload,
            headers={
                "Authorization": (f"Bearer {self.config.bridge_inbound_token}"),
            },
        )
        if response.is_success:
            logger.info(
                "Forwarded Discord bridge message %s from channel %s",
                message.id,
                message.channel.id,
            )
            return
        logger.warning(
            "Discord bridge inbound sync failed: status=%s body=%s",
            response.status_code,
            response.text,
        )

    # --- Bridge channel detection ---

    def _is_bridge_channel_message(
        self,
        message: discord.Message,
    ) -> bool:
        """Return whether one Discord message belongs to the configured bridge."""
        if not self._has_bridge_intake_config():
            return False
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return False
        if str(channel.category_id or "") != self.config.bridge_category_id:
            return False
        guild = getattr(message, "guild", None)
        if guild is None:
            return False
        return str(guild.id) == self.config.bridge_external_space_id

    def _has_bridge_intake_config(self) -> bool:
        """Return whether bridge-channel intake is fully configured."""
        return all(
            (
                self.config.bridge_category_id,
                self.config.bridge_external_space_id,
                self.config.bridge_inbound_url,
                self.config.bridge_inbound_token,
            )
        )

    def _build_bridge_inbound_url(self) -> str:
        """Build the full FastAPI inbound URL for Discord bridge messages."""
        return self.config.bridge_inbound_url.rstrip("/") + BRIDGE_ROUTE_PATH

    # --- Image extraction ---

    async def _extract_inline_images(
        self,
        message: discord.Message,
    ) -> list[str]:
        """Download supported image attachments as inline data URLs."""
        images: list[str] = []
        for attachment in message.attachments:
            if (
                not attachment.content_type
                or attachment.content_type.split(";")[0] not in IMAGE_MIME_TYPES
            ):
                continue
            if attachment.size and attachment.size > MAX_INLINE_IMAGE_SIZE_BYTES:
                continue
            if len(images) >= 4:
                break
            try:
                data = await attachment.read()
                mime = attachment.content_type.split(";")[0]
                b64 = base64.b64encode(data).decode()
                images.append(f"data:{mime};base64,{b64}")
            except RECOVERABLE_ATTACHMENT_ERRORS:
                logger.warning(
                    "Failed to download attachment %s",
                    attachment.filename,
                    exc_info=True,
                )
        return images

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the Discord client as a background task."""
        logger.info("Starting Discord bot")
        self._client_task = asyncio.create_task(
            self._client.start(self.config.bot_token),
        )

    async def stop(self) -> None:
        """Stop the Discord client and clean up resources."""
        logger.info("Stopping Discord bot")
        await self._client.close()
        if self._owns_bridge_http_client and self._bridge_http_client is not None:
            await self._bridge_http_client.aclose()
            self._bridge_http_client = None
        if self._client_task is not None:
            self._client_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._client_task
            self._client_task = None
