"""MCP (Model Context Protocol) provider for external chat bridge links.

Pull-only adapter: MCP agents initiate calls into biMEMO. There is no
outbound push channel, so ``send_message`` and ``create_bridge_target``
are inert no-ops. The provider is configured by the MCP master switch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import BridgeTarget, OutboundBridgeMessage, ProviderMessageRef
from .base import ChatBridgeProviderBase


if TYPE_CHECKING:
    from backend.config import Settings


class McpChatBridgeProvider(ChatBridgeProviderBase):
    """MCP bridge provider — pull-only, outbound operations are inert.

    MCP agents authenticate via bearer tokens (SHA-256 hashed).
    The provider is considered "configured" when the master MCP switch
    (``BIMEMO_MCP_ENABLED``) is True. There is no outbound channel,
    so ``send_message`` and ``create_bridge_target`` produce no external
    side-effect.
    """

    @staticmethod
    def is_configured(settings: Settings) -> bool:
        """Return whether the MCP master switch is enabled."""
        return bool(settings.mcp_enabled)

    @classmethod
    def build_from_settings(
        cls,
        settings: Settings,
    ) -> ChatBridgeProviderBase:
        """Construct a zero-config MCP provider instance."""
        return cls()

    @staticmethod
    def validate_inbound_server(
        settings: Settings,
        external_space_id: str,
    ) -> bool:
        """Always accept inbound — agent identity comes from bearer tokens."""
        del settings
        del external_space_id
        return True

    @staticmethod
    def allowed_inbound_user_ids(settings: Settings) -> set[str]:
        """Return an empty set — identity is enforced by bearer tokens."""
        del settings
        return set()

    @staticmethod
    def default_persona_user_id(allowed_ids: frozenset[str]) -> str:
        """Return empty — no persona mapping for MCP agents."""
        del allowed_ids
        return ""

    @staticmethod
    def server_mismatch_detail() -> str:
        """Return a no-op detail — MCP has no inbound server concept."""
        return "MCP has no inbound server validation"

    @staticmethod
    def user_forbidden_detail() -> str:
        """Return a no-op detail — MCP authorisation is token-based."""
        return "MCP uses bearer tokens, not user IDs"

    async def create_bridge_target(self, *, title: str) -> BridgeTarget:
        """Return a local (biMEMO-only) target — no external channel is created.

        Args:
            title: Ignored — no external channel is created.

        Returns:
            A BridgeTarget with local placeholder identifiers.
        """
        del title
        return BridgeTarget(
            external_space_id="mcp",
            external_channel_id="mcp",
        )

    async def send_message(
        self,
        *,
        target: BridgeTarget,
        message: OutboundBridgeMessage,
    ) -> ProviderMessageRef:
        """No-op — MCP has no outbound push channel.

        The answer to ``ask`` is returned as the synchronous tool result,
        never delivered through an outbound send.

        Args:
            target: Ignored — no external send occurs.
            message: Ignored — no external send occurs.

        Returns:
            A local placeholder ProviderMessageRef.
        """
        del target
        del message
        return ProviderMessageRef(external_message_id="mcp-no-op")
