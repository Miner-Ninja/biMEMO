"""Pydantic response models for all API endpoints.

Defines response schemas for serialization. Models ensure only
declared fields are included in JSON output.
"""

from importlib import import_module
from typing import TYPE_CHECKING, TypedDict

from pydantic import BaseModel, ConfigDict


if TYPE_CHECKING:
    from .chat_stats import ExtendedUsage


class StrictResponseModel(BaseModel):
    """Base response model with strict field validation."""

    model_config = ConfigDict(extra="forbid")


class RetrievedDebugItem(TypedDict, total=False):
    """Debug metadata returned for retrieved memories and messages."""

    summary: str
    main_point: str
    date: str
    role: str
    content: str
    conversation_id: str


class ConfirmationItem(StrictResponseModel):
    """Pending confirmation action.

    Attributes:
        action_id: Cryptographic action identifier.
        tool_name: Name of the tool awaiting confirmation.
        parameters: Tool call parameters.
    """

    action_id: str
    tool_name: str
    parameters: dict[str, object]


class StructuredAssistantContent(StrictResponseModel):
    """Structured assistant content extracted from one final response."""

    answer: str
    thinking: str | None = None
    sql: str | None = None
    suggestions: list[str] = []


class ChatResponse(StrictResponseModel):
    """Chat endpoint response.

    Attributes:
        input_filtered: Whether input content filter was triggered.
        output_filtered: Whether output content filter was triggered.
        pending_confirmations: RED zone actions awaiting approval.
        retrieved_memories: Summaries of search-matched records.
        retrieved_messages: Debug metadata for FTS-matched messages.
    """

    conversation_id: str = ""
    input_filtered: bool
    output_filtered: bool
    pending_confirmations: list[ConfirmationItem]
    retrieved_memories: list[RetrievedDebugItem] | None = None
    retrieved_messages: list[RetrievedDebugItem] | None = None
    transcription: str | None = None
    stats: ExtendedUsage | None = None
    structured_content: StructuredAssistantContent | None = None
    stream_enabled: bool = False


class ChatStartResponse(StrictResponseModel):
    """Chat start acknowledgement.

    The final assistant response is delivered by the stream `final` event.
    """

    conversation_id: str
    request_id: str
    run_id: str = ""
    stream_started: bool = True


class StopResponse(StrictResponseModel):
    """Stop endpoint response.

    Attributes:
        stopped: Whether an active loop was stopped.
    """

    stopped: bool
    run_id: str = ""


class IntegrationListItem(StrictResponseModel):
    """Integration list entry.

    Attributes:
        name: Integration name.
        type: Integration type.
    """

    name: str
    type: str


class SummaryItem(StrictResponseModel):
    """Summary list entry.

    Attributes:
        main_point: Summary main point text.
        trust_level: Trust level label.
        scope: Summary time horizon.
        based_on_count: Number of linked records.
        created_at: ISO 8601 creation timestamp.
    """

    main_point: str
    trust_level: str
    scope: str
    based_on_count: int
    created_at: str


class ChatSettingsResponse(StrictResponseModel):
    """Per-conversation display settings."""

    show_sql: bool = False
    show_thinking: bool = False
    show_suggestions: bool = True


class ConversationItem(StrictResponseModel):
    """Conversation list entry returned by chat read APIs."""

    id: str
    title: str
    bridge_provider: str | None = None
    settings: ChatSettingsResponse = ChatSettingsResponse()
    processing_status: str = "idle"
    processing_stage: str = ""
    processing_started_at: str | None = None
    created_at: str
    updated_at: str


class ConversationCreateResponse(StrictResponseModel):
    """Conversation creation response."""

    conversation_id: str


class ConversationDeleteResponse(StrictResponseModel):
    """Conversation deletion response."""

    ok: bool


class ConversationMessageItem(StrictResponseModel):
    """One persisted message inside a conversation."""

    id: str
    role: str
    content: str | list[dict[str, object]]
    conversation_id: str
    created_at: str
    meta: str | None = None
    structured_content: StructuredAssistantContent | None = None


class ChatBridgeLinkItem(StrictResponseModel):
    """External chat bridge link returned by admin routes.

    Attributes:
        id: Internal bridge link identifier.
        conversation_id: Linked web conversation identifier.
        provider: External provider name.
        external_space_id: Parent external room or guild scope identifier.
        external_channel_id: External room or channel identifier.
        created_by: Internal creator identifier.
        sync_direction: Enabled synchronization direction.
        status: Current bridge state.
        delivery_mode: External delivery presentation mode.
        persona_mode: User persona resolution mode.
        persona_user_id: Explicit provider-side user identifier for mapped persona mode.
        created_at: ISO 8601 creation timestamp.
    """

    id: str
    conversation_id: str
    provider: str
    external_space_id: str
    external_channel_id: str
    created_by: str
    sync_direction: str
    status: str
    delivery_mode: str
    persona_mode: str
    persona_user_id: str
    created_at: str


class ChatBridgeProviderItem(StrictResponseModel):
    """Configured external bridge provider that can be chosen in the web UI."""

    provider: str
    default_persona_user_id: str = ""


class ChatBridgeInboundMessageResponse(StrictResponseModel):
    """Inbound bridge synchronization response.

    Attributes:
        message_id: Internal saved message identifier.
        synced: Whether the inbound message was accepted and stored.
    """

    message_id: str
    synced: bool
    reason: str = ""


class ChatBridgeMessageLinkItem(StrictResponseModel):
    """Stored bridge message mapping returned by admin routes.

    Attributes:
        id: Internal mapping identifier.
        bridge_link_id: Parent bridge link identifier.
        message_id: Internal biMEMO message identifier.
        external_message_id: Provider-side message identifier.
        direction: Bridge direction of this mapping.
        created_at: ISO 8601 creation timestamp.
    """

    id: str
    bridge_link_id: str
    message_id: str
    external_message_id: str
    direction: str
    created_at: str


ChatResponse.model_rebuild(
    _types_namespace={
        "ExtendedUsage": import_module("backend.models.chat_stats").ExtendedUsage
    }
)
