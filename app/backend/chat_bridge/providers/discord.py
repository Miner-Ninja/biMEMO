"""Discord provider implementation for external chat bridge links."""

import base64
import json
import logging
import re
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import httpx

from ..models import BridgeTarget, OutboundBridgeMessage, ProviderMessageRef
from .base import ChatBridgeProviderBase


if TYPE_CHECKING:
    from backend.config import Settings


DISCORD_API_BASE_URL = "https://discord.com/api/v10"
DEFAULT_HTTP_TIMEOUT_SECONDS = 15.0
GUILD_TEXT_CHANNEL_TYPE = 0
MAX_CHANNEL_NAME_LENGTH = 100
MAX_MESSAGE_CONTENT_LENGTH = 2000
WEBHOOK_NAME = "biMEMO Web Bridge"
WHITESPACE_PATTERN = re.compile(r"\s+")
NON_WORD_PATTERN = re.compile(r"[^\w-]+", flags=re.UNICODE)
MULTI_DASH_PATTERN = re.compile(r"-{2,}")


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiscordBridgeConfig:
    """Configuration for Discord channel creation and message posting.

    Attributes:
        bot_token: Discord bot token.
        category_id: Discord category where new bridge channels are created.
        external_space_id: Stable identifier for the configured Discord guild.
        base_url: Discord REST API base URL.
    """

    bot_token: str
    category_id: str
    external_space_id: str
    base_url: str = DISCORD_API_BASE_URL


@dataclass(frozen=True, slots=True)
class DiscordPersona:
    """Resolved Discord display persona for one user."""

    display_name: str
    avatar_url: str


class DiscordChatBridgeProvider(ChatBridgeProviderBase):
    """Create Discord channels and post synchronized bridge messages."""

    def __init__(
        self,
        *,
        config: DiscordBridgeConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the Discord bridge provider."""
        self._config = config
        self._client = client
        self._webhook_cache: dict[str, str] = {}
        self._persona_cache: dict[str, DiscordPersona] = {}

    @staticmethod
    def is_configured(settings: Settings) -> bool:
        """Return whether Discord bridge has all required settings."""
        return bool(
            settings.discord_bot_token
            and settings.discord_bridge_category_id
            and settings.discord_bridge_server_id
        )

    @classmethod
    def build_from_settings(
        cls,
        settings: Settings,
    ) -> ChatBridgeProviderBase:
        """Construct a Discord provider from app settings."""
        return cls(
            config=DiscordBridgeConfig(
                bot_token=settings.discord_bot_token,
                category_id=settings.discord_bridge_category_id,
                external_space_id=settings.discord_bridge_server_id,
            ),
        )

    @staticmethod
    def validate_inbound_server(
        settings: Settings,
        external_space_id: str,
    ) -> bool:
        """Return whether the inbound guild matches the configured server."""
        configured = str(settings.discord_bridge_server_id).strip()
        if not configured:
            return True
        return external_space_id == configured

    @staticmethod
    def allowed_inbound_user_ids(settings: Settings) -> set[str]:
        """Return Discord user IDs allowed for inbound bridge messages."""
        from backend.config import parse_configured_id_list

        return set(
            parse_configured_id_list(settings.discord_allowed_user_ids),
        )

    @staticmethod
    def default_persona_user_id(
        allowed_ids: frozenset[str],
    ) -> str:
        """Return the single allowed ID as default persona if unambiguous."""
        if len(allowed_ids) != 1:
            return ""
        return next(iter(allowed_ids))

    @staticmethod
    def server_mismatch_detail() -> str:
        """Return error detail for Discord server mismatch."""
        return "Discord bridge server mismatch"

    @staticmethod
    def user_forbidden_detail() -> str:
        """Return error detail for unauthorized Discord author."""
        return "Discord author is not allowed for bridge sync"

    @asynccontextmanager
    async def _request_client(self) -> httpx.AsyncClient:
        """Yield the configured HTTP client and close temporary clients safely."""
        client = self._client
        should_close_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
            )
        try:
            yield client
        finally:
            if should_close_client:
                await client.aclose()

    @staticmethod
    def _webhook_url(base_url: str, webhook_id: str, webhook_token: str) -> str:
        """Build one Discord webhook URL."""
        return f"{base_url}/webhooks/{webhook_id}/{webhook_token}"

    @staticmethod
    def _discord_avatar_url(user_id: str, avatar_hash: str) -> str:
        """Build one Discord CDN avatar URL."""
        return (
            f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=256"
        )

    @staticmethod
    def _webhook_url_from_payload(
        *,
        base_url: str,
        payload: object,
    ) -> str | None:
        """Extract one reusable webhook URL from a Discord payload."""
        if not isinstance(payload, dict):
            return None
        webhook_id = str(payload.get("id", "")).strip()
        webhook_token = str(payload.get("token", "")).strip()
        if not webhook_id or not webhook_token:
            return None
        return DiscordChatBridgeProvider._webhook_url(
            base_url,
            webhook_id,
            webhook_token,
        )

    @staticmethod
    def _named_webhook_url(
        *,
        base_url: str,
        payload: object,
        webhook_name: str,
    ) -> str | None:
        """Return one matching webhook URL from a listed webhook payload."""
        if not isinstance(payload, dict):
            return None
        if str(payload.get("name", "")) != webhook_name:
            return None
        return DiscordChatBridgeProvider._webhook_url_from_payload(
            base_url=base_url,
            payload=payload,
        )

    @staticmethod
    def _resolved_persona(
        *,
        user_id: str,
        user_payload: object,
    ) -> DiscordPersona | None:
        """Build one Discord persona from a user payload."""
        if not isinstance(user_payload, dict):
            return None
        display_name = str(
            user_payload.get("global_name") or user_payload.get("username") or user_id
        ).strip()
        avatar_hash = str(user_payload.get("avatar", "")).strip()
        avatar_url = ""
        if avatar_hash:
            avatar_url = DiscordChatBridgeProvider._discord_avatar_url(
                user_id,
                avatar_hash,
            )
        return DiscordPersona(
            display_name=display_name or "Web User",
            avatar_url=avatar_url,
        )

    async def create_bridge_target(self, *, title: str) -> BridgeTarget:
        """Create a Discord text channel for one linked conversation."""
        payload = {
            "name": self._normalize_channel_name(title),
            "type": GUILD_TEXT_CHANNEL_TYPE,
            "parent_id": self._config.category_id,
        }
        response_payload = await self._request_json(
            "POST",
            f"/guilds/{self._config.external_space_id}/channels",
            json=payload,
        )
        channel_id = str(response_payload["id"])
        return BridgeTarget(
            external_space_id=self._config.external_space_id,
            external_channel_id=channel_id,
        )

    async def send_message(
        self,
        *,
        target: BridgeTarget,
        message: OutboundBridgeMessage,
    ) -> ProviderMessageRef:
        """Send one message with optional image attachments."""
        if message.use_persona:
            return await self._send_webhook_messages(
                target=target,
                message=message,
            )
        return await self._send_bot_messages(
            target=target,
            content=message.content,
            images=message.images,
        )

    @staticmethod
    def _message_content_chunks(content: str) -> list[str]:
        """Split one Discord message into chunks that fit the content limit."""
        if len(content) <= MAX_MESSAGE_CONTENT_LENGTH:
            return [content]
        chunks: list[str] = []
        remaining = content
        while len(remaining) > MAX_MESSAGE_CONTENT_LENGTH:
            split_at = remaining.rfind("\n", 0, MAX_MESSAGE_CONTENT_LENGTH + 1)
            if split_at <= 0:
                split_at = remaining.rfind(" ", 0, MAX_MESSAGE_CONTENT_LENGTH + 1)
            if split_at <= 0:
                split_at = MAX_MESSAGE_CONTENT_LENGTH
            chunk = remaining[:split_at].rstrip()
            if not chunk:
                chunk = remaining[:MAX_MESSAGE_CONTENT_LENGTH]
                split_at = MAX_MESSAGE_CONTENT_LENGTH
            chunks.append(chunk)
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    @staticmethod
    def _decode_data_url(data_url: str) -> tuple[bytes, str]:
        """Decode a base64 data URL into raw bytes and file extension."""
        header, b64_data = data_url.split(",", 1)
        mime = header.split(":")[1].split(";")[0]
        ext = mime.split("/")[1] if "/" in mime else "png"
        return base64.b64decode(b64_data), ext

    async def _send_bot_messages(
        self,
        *,
        target: BridgeTarget,
        content: str,
        images: list[str] | None = None,
    ) -> ProviderMessageRef:
        """Send one or more bridge messages through the normal bot identity."""
        last_message_ref: ProviderMessageRef | None = None
        for chunk in self._message_content_chunks(content):
            response_payload = await self._request_json(
                "POST",
                f"/channels/{target.external_channel_id}/messages",
                json={"content": chunk},
            )
            last_message_ref = ProviderMessageRef(
                external_message_id=str(response_payload["id"]),
            )
        if images:
            ref = await self._send_image_attachments(
                channel_id=target.external_channel_id,
                images=images,
                content=content if last_message_ref is None else "",
            )
            if ref is not None:
                last_message_ref = ref
        if last_message_ref is None:
            raise RuntimeError(
                "Discord message sending returned no provider message",
            )
        return last_message_ref

    async def _send_image_attachments(
        self,
        *,
        channel_id: str,
        images: list[str],
        content: str = "",
    ) -> ProviderMessageRef | None:
        """Send images as file attachments to a Discord channel."""
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for idx, data_url in enumerate(images):
            try:
                raw_bytes, ext = self._decode_data_url(data_url)
                filename = f"image_{idx}.{ext}"
                mime = f"image/{ext}"
                files.append((f"files[{idx}]", (filename, raw_bytes, mime)))
            except ValueError, base64.binascii.Error:
                logger.warning("Skipping malformed image data URL")
                continue
        if not files:
            return None
        payload_json = {"content": content} if content else {}
        async with self._request_client() as client:
            response = await client.post(
                f"/channels/{channel_id}/messages",
                headers={
                    "Authorization": f"Bot {self._config.bot_token}",
                },
                data={"payload_json": json.dumps(payload_json)},
                files=files,
            )
            response.raise_for_status()
            result = response.json()
        return ProviderMessageRef(
            external_message_id=str(result["id"]),
        )

    async def _send_webhook_messages(
        self,
        *,
        target: BridgeTarget,
        message: OutboundBridgeMessage,
    ) -> ProviderMessageRef:
        """Send one or more user-authored messages through a Discord webhook persona."""
        webhook_url = await self._get_or_create_webhook_url(
            channel_id=target.external_channel_id
        )
        last_message_ref: ProviderMessageRef | None = None
        for chunk in self._message_content_chunks(message.content):
            payload = await self._webhook_payload(
                replace(message, content=chunk),
            )
            response_payload = await self._request_json(
                "POST",
                f"{webhook_url}?wait=true",
                json=payload,
                use_bot_authorization=False,
            )
            last_message_ref = ProviderMessageRef(
                external_message_id=str(response_payload["id"])
            )
        if message.images:
            ref = await self._send_image_attachments(
                channel_id=target.external_channel_id,
                images=message.images,
                content=(message.content if last_message_ref is None else ""),
            )
            if ref is not None:
                last_message_ref = ref
        if last_message_ref is None:
            raise RuntimeError(
                "Discord webhook sending returned no provider message",
            )
        return last_message_ref

    async def _get_or_create_webhook_url(self, *, channel_id: str) -> str:
        """Return one cached or persisted Discord webhook URL for a bridge channel."""
        cached_url = self._webhook_cache.get(channel_id)
        if cached_url:
            return cached_url
        webhooks_payload = await self._request_json(
            "GET",
            f"/channels/{channel_id}/webhooks",
            json=None,
        )
        if isinstance(webhooks_payload, list):
            for item in webhooks_payload:
                webhook_url = self._named_webhook_url(
                    base_url=self._config.base_url,
                    payload=item,
                    webhook_name=WEBHOOK_NAME,
                )
                if webhook_url:
                    self._webhook_cache[channel_id] = webhook_url
                    return webhook_url
        created_payload = await self._request_json(
            "POST",
            f"/channels/{channel_id}/webhooks",
            json={"name": WEBHOOK_NAME},
        )
        webhook_url = self._webhook_url_from_payload(
            base_url=self._config.base_url,
            payload=created_payload,
        )
        if webhook_url is None:
            raise RuntimeError("Discord webhook creation did not return a usable URL")
        self._webhook_cache[channel_id] = webhook_url
        return webhook_url

    async def _webhook_payload(
        self,
        message: OutboundBridgeMessage,
    ) -> dict[str, object]:
        """Build one Discord webhook payload with optional persona overrides."""
        payload: dict[str, object] = {
            "content": message.content,
            "username": message.display_name or "Web User",
        }
        persona = await self._message_persona(message)
        if persona is not None:
            payload["username"] = persona.display_name or str(payload["username"])
            if persona.avatar_url:
                payload["avatar_url"] = persona.avatar_url
            return payload
        if message.avatar_url:
            payload["avatar_url"] = message.avatar_url
        return payload

    async def _message_persona(
        self,
        message: OutboundBridgeMessage,
    ) -> DiscordPersona | None:
        """Resolve the Discord persona requested by one outbound bridge message."""
        normalized_user_id = message.persona_user_id.strip()
        if not normalized_user_id:
            return None
        return await self._resolve_user_persona(user_id=normalized_user_id)

    async def _resolve_user_persona(self, *, user_id: str) -> DiscordPersona | None:
        """Resolve one explicit Discord persona by provider-side user identifier."""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            return None
        cached_persona = self._persona_cache.get(normalized_user_id)
        if cached_persona is not None:
            return cached_persona
        user_payload = await self._request_json(
            "GET",
            f"/users/{normalized_user_id}",
            json=None,
        )
        persona = self._resolved_persona(
            user_id=normalized_user_id,
            user_payload=user_payload,
        )
        if persona is None:
            return None
        self._persona_cache[normalized_user_id] = persona
        return persona

    def _normalize_channel_name(self, title: str) -> str:
        """Return a Discord-safe text channel name."""
        normalized = unicodedata.normalize("NFKC", title.strip().lower())
        normalized = WHITESPACE_PATTERN.sub("-", normalized)
        normalized = normalized.replace("_", "-")
        normalized = NON_WORD_PATTERN.sub("-", normalized)
        normalized = MULTI_DASH_PATTERN.sub("-", normalized)
        normalized = normalized.strip("-")
        if not normalized:
            normalized = "bimemo-bridge"
        return normalized[:MAX_CHANNEL_NAME_LENGTH]

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None,
        use_bot_authorization: bool = True,
    ) -> dict[str, object] | list[object]:
        """Send one Discord REST request and return the JSON response."""
        async with self._request_client() as client:
            response = await client.request(
                method,
                path,
                headers=self._build_headers(use_bot_authorization),
                json=json,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict | list):
                raise RuntimeError("Discord API returned an unexpected JSON payload")
            return payload

    def _build_headers(self, use_bot_authorization: bool) -> dict[str, str]:
        """Build headers for one Discord REST request."""
        headers = {"Content-Type": "application/json"}
        if use_bot_authorization:
            headers["Authorization"] = f"Bot {self._config.bot_token}"
        return headers
