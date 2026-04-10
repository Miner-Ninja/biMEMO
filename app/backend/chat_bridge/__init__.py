"""Provider-neutral chat bridge models, services, and adapters."""

from .models import (
    BridgeDeliveryMode,
    BridgeMessageDirection,
    BridgePersonaMode,
    BridgeProvider,
    BridgeStatus,
    BridgeSyncDirection,
    BridgeTarget,
    ChatBridgeLink,
    ChatBridgeMessageLink,
    ExternalBridgeMessage,
    ProviderMessageRef,
)
from .provider import ChatBridgeProvider
from .service import ChatBridgeService
from .service_models import InboundBridgeSyncResult


__all__ = [
    "BridgeDeliveryMode",
    "BridgeMessageDirection",
    "BridgePersonaMode",
    "BridgeProvider",
    "BridgeStatus",
    "BridgeSyncDirection",
    "BridgeTarget",
    "ChatBridgeLink",
    "ChatBridgeMessageLink",
    "ChatBridgeProvider",
    "ChatBridgeService",
    "ExternalBridgeMessage",
    "InboundBridgeSyncResult",
    "ProviderMessageRef",
]
