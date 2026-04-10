"""Abstract base for external chat bridge provider implementations.

Defines the contract that every bridge provider must implement.
Bridge core (service, models, routes) depends only on this interface,
never on any concrete provider. All provider-specific configuration,
validation, persona logic, and error messages live in provider modules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.config import Settings

    from ..models import BridgeTarget, OutboundBridgeMessage, ProviderMessageRef


class ChatBridgeProviderBase(ABC):
    """Abstract base class for external chat bridge providers.

    Attributes:
        provider_name: Stable lower-case identifier used in BridgeProvider enum.
    """

    @staticmethod
    @abstractmethod
    def is_configured(settings: Settings) -> bool:
        """Return whether this provider has all required settings present."""

    @classmethod
    @abstractmethod
    def build_from_settings(cls, settings: Settings) -> ChatBridgeProviderBase:
        """Construct a fully configured provider instance from app settings."""

    @staticmethod
    @abstractmethod
    def validate_inbound_server(
        settings: Settings,
        external_space_id: str,
    ) -> bool:
        """Return whether the inbound server matches the configured scope."""

    @staticmethod
    @abstractmethod
    def allowed_inbound_user_ids(settings: Settings) -> set[str]:
        """Return the set of author IDs allowed to send inbound messages."""

    @staticmethod
    @abstractmethod
    def default_persona_user_id(allowed_ids: frozenset[str]) -> str:
        """Return the default persona user ID for bridge creation."""

    @staticmethod
    @abstractmethod
    def server_mismatch_detail() -> str:
        """Return an error detail string for inbound server validation failure."""

    @staticmethod
    @abstractmethod
    def user_forbidden_detail() -> str:
        """Return an error detail string for unauthorized inbound author."""

    @abstractmethod
    async def create_bridge_target(self, *, title: str) -> BridgeTarget:
        """Create one external channel or room for a new bridge link."""

    @abstractmethod
    async def send_message(
        self,
        *,
        target: BridgeTarget,
        message: OutboundBridgeMessage,
    ) -> ProviderMessageRef:
        """Send one message into an external bridge channel."""
