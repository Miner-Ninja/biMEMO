"""Provider implementations for external chat bridge adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import BridgeProvider
from .base import ChatBridgeProviderBase
from .discord import DiscordChatBridgeProvider


if TYPE_CHECKING:
    from backend.config import Settings


# --- Provider registry ---
REGISTERED_PROVIDERS: dict[BridgeProvider, type[ChatBridgeProviderBase]] = {
    BridgeProvider.DISCORD: DiscordChatBridgeProvider,
}


def build_configured_providers(
    settings: Settings,
) -> dict[BridgeProvider, ChatBridgeProviderBase]:
    """Build provider instances for all providers with valid configuration."""
    providers: dict[BridgeProvider, ChatBridgeProviderBase] = {}
    for provider_key, provider_class in REGISTERED_PROVIDERS.items():
        if provider_class.is_configured(settings):
            providers[provider_key] = provider_class.build_from_settings(settings)
    return providers


def get_provider_class(
    provider: BridgeProvider,
) -> type[ChatBridgeProviderBase] | None:
    """Return the registered provider class for one bridge provider."""
    return REGISTERED_PROVIDERS.get(provider)


__all__ = [
    "REGISTERED_PROVIDERS",
    "ChatBridgeProviderBase",
    "DiscordChatBridgeProvider",
    "build_configured_providers",
    "get_provider_class",
]
