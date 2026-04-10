"""Provider contract for external chat bridge adapters."""

from typing import Protocol

from .models import BridgeTarget, OutboundBridgeMessage, ProviderMessageRef


BridgeMessageTarget = BridgeTarget
BridgeOutboundMessage = OutboundBridgeMessage


class ChatBridgeProvider(Protocol):
    """Provider contract for one external messenger bridge."""

    async def create_bridge_target(self, *, title: str) -> BridgeTarget:
        """Create a provider-side room or channel for one conversation."""

    async def send_message(
        self,
        *,
        target: BridgeMessageTarget,
        message: BridgeOutboundMessage,
    ) -> ProviderMessageRef:
        """Send one message into an existing provider-side bridge target."""
