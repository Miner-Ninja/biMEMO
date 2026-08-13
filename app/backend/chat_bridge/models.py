"""Typed models for external chat bridge links and message mappings."""

from dataclasses import dataclass, field
from enum import StrEnum


class BridgeProvider(StrEnum):
    """Supported external chat bridge providers."""

    DISCORD = "discord"
    MCP = "mcp"


class BridgeSyncDirection(StrEnum):
    """Supported synchronization directions for a bridge link."""

    WEB_TO_EXTERNAL = "web_to_external"
    EXTERNAL_TO_WEB = "external_to_web"
    BIDIRECTIONAL = "bidirectional"


class BridgeStatus(StrEnum):
    """Supported operational states for a bridge link."""

    ACTIVE = "active"
    DISABLED = "disabled"


class BridgeDeliveryMode(StrEnum):
    """How bridge messages should be presented on the external side."""

    BOT_ONLY = "bot_only"
    PERSONA_USER_AND_BOT_ASSISTANT = "persona_user_and_bot_assistant"


class BridgePersonaMode(StrEnum):
    """How a user-authored bridge persona should be resolved."""

    NEUTRAL = "neutral"
    MAPPED_USER = "mapped_user"


class BridgeMessageDirection(StrEnum):
    """Direction of one synchronized bridge message."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"


def _payload_string(payload: dict[str, object], key: str, default: str = "") -> str:
    """Return one payload field as a string."""
    return str(payload.get(key, default))


def _payload_enum[BridgeEnum: StrEnum](
    payload: dict[str, object],
    key: str,
    enum_type: type[BridgeEnum],
) -> BridgeEnum:
    """Return one payload field as the requested string enum."""
    return enum_type(_payload_string(payload, key))


@dataclass(frozen=True, slots=True)
class BridgeTarget:
    """Provider-neutral external target identifiers.

    Attributes:
        external_space_id: Parent external room or guild scope identifier.
        external_channel_id: External room or channel identifier.
    """

    external_space_id: str
    external_channel_id: str


@dataclass(frozen=True, slots=True)
class ProviderMessageRef:
    """Reference to one provider-side message.

    Attributes:
        external_message_id: Provider message identifier.
    """

    external_message_id: str


@dataclass(frozen=True, slots=True)
class OutboundBridgeMessage:
    """One outbound bridge message with sender presentation details.

    Attributes:
        role: Internal sender role for the message.
        content: Plain text content to send outward.
        images: Base64 data URLs of attached images.
        display_name: Preferred sender display name for provider presentation.
        avatar_url: Optional sender avatar URL for provider-side persona rendering.
        use_persona: Whether this message should be sent through persona delivery.
        persona_user_id: Explicit provider user identifier for mapped persona mode.
    """

    role: str
    content: str
    images: list[str] = field(default_factory=list)
    display_name: str = ""
    avatar_url: str = ""
    use_persona: bool = False
    persona_user_id: str = ""


@dataclass(frozen=True, slots=True)
class ChatBridgeLink:
    """Stored relation between one web conversation and one external target.

    Attributes:
        id: Internal bridge link identifier.
        conversation_id: Linked web conversation identifier.
        provider: External provider name.
        external_space_id: Parent external room or guild scope identifier.
        external_channel_id: External room or channel identifier.
        created_by: Internal user or session identifier that created the link.
        sync_direction: Enabled synchronization direction.
        status: Current link state.
        delivery_mode: External delivery presentation mode.
        persona_mode: User persona resolution mode.
        persona_user_id: Explicit provider-side user identifier for mapped persona mode.
        created_at: ISO 8601 creation timestamp.
    """

    id: str
    conversation_id: str
    provider: BridgeProvider
    external_space_id: str
    external_channel_id: str
    created_by: str
    sync_direction: BridgeSyncDirection
    status: BridgeStatus
    delivery_mode: BridgeDeliveryMode
    persona_mode: BridgePersonaMode
    persona_user_id: str
    created_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ChatBridgeLink:
        """Build a typed bridge link from DB or API payload data."""
        return cls(
            id=_payload_string(payload, "id"),
            conversation_id=_payload_string(payload, "conversation_id"),
            provider=_payload_enum(payload, "provider", BridgeProvider),
            external_space_id=_payload_string(payload, "external_space_id"),
            external_channel_id=_payload_string(payload, "external_channel_id"),
            created_by=_payload_string(payload, "created_by"),
            sync_direction=_payload_enum(
                payload,
                "sync_direction",
                BridgeSyncDirection,
            ),
            status=_payload_enum(payload, "status", BridgeStatus),
            delivery_mode=_payload_enum(
                payload,
                "delivery_mode",
                BridgeDeliveryMode,
            ),
            persona_mode=_payload_enum(payload, "persona_mode", BridgePersonaMode),
            persona_user_id=_payload_string(payload, "persona_user_id"),
            created_at=_payload_string(payload, "created_at"),
        )

    def _allows_sync_direction(
        self,
        *directions: BridgeSyncDirection,
    ) -> bool:
        """Return whether this active link allows one of the given directions."""
        if self.status != BridgeStatus.ACTIVE:
            return False
        return self.sync_direction in directions

    def allows_outbound_sync(self) -> bool:
        """Return whether web-to-external synchronization is enabled."""
        return self._allows_sync_direction(
            BridgeSyncDirection.WEB_TO_EXTERNAL,
            BridgeSyncDirection.BIDIRECTIONAL,
        )

    def allows_inbound_sync(self) -> bool:
        """Return whether external-to-web synchronization is enabled."""
        return self._allows_sync_direction(
            BridgeSyncDirection.EXTERNAL_TO_WEB,
            BridgeSyncDirection.BIDIRECTIONAL,
        )

    def target(self) -> BridgeTarget:
        """Return the provider target identifiers for this link."""
        return BridgeTarget(
            external_space_id=self.external_space_id,
            external_channel_id=self.external_channel_id,
        )


@dataclass(frozen=True, slots=True)
class ChatBridgeMessageLink:
    """Stored relation between one internal and one external message.

    Attributes:
        id: Internal bridge message mapping identifier.
        bridge_link_id: Parent bridge link identifier.
        message_id: Internal or external-origin message identifier.
        external_message_id: Provider message identifier.
        direction: Synchronization direction of this mapping.
        created_at: ISO 8601 creation timestamp.
    """

    id: str
    bridge_link_id: str
    message_id: str
    external_message_id: str
    direction: BridgeMessageDirection
    created_at: str
    finalization_id: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ChatBridgeMessageLink:
        """Build a typed message mapping from DB or API payload data."""
        return cls(
            id=_payload_string(payload, "id"),
            bridge_link_id=_payload_string(payload, "bridge_link_id"),
            message_id=_payload_string(payload, "message_id"),
            external_message_id=_payload_string(payload, "external_message_id"),
            direction=_payload_enum(payload, "direction", BridgeMessageDirection),
            finalization_id=_payload_string(payload, "finalization_id"),
            created_at=_payload_string(payload, "created_at"),
        )


@dataclass(frozen=True, slots=True)
class ExternalBridgeMessage:
    """Inbound external message that should enter a web conversation.

    Attributes:
        provider: External provider name.
        external_space_id: Parent external room or guild scope identifier.
        external_channel_id: External room or channel identifier.
        external_message_id: Provider message identifier.
        author_id: Provider author identifier.
        author_name: Provider author display name.
        content: Plain text content.
        images: Base64 data URLs of attached images.
    """

    provider: BridgeProvider
    external_space_id: str
    external_channel_id: str
    external_message_id: str
    author_id: str
    author_name: str
    content: str
    images: list[str] = field(default_factory=list)
