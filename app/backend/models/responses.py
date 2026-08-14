"""Pydantic response models for all API endpoints.

Defines response schemas for serialization. Models ensure only
declared fields are included in JSON output.
"""

from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        grant_capable: True when the permission mode allows a reusable
            grant (conversation_capability or scoped).
        url_host: Canonical server host derived for domain-scoped permissions.
    """

    action_id: str
    tool_name: str
    parameters: dict[str, object]
    grant_capable: bool = False
    url_host: str | None = None


class UsedToolItem(StrictResponseModel):
    """Record of one tool execution.

    Attributes:
        name: Tool name that was executed.
        permission_mode: Registry permission mode used for execution.
        success: Whether the tool executed successfully.
    """

    name: str
    permission_mode: str = ""
    authorization_source: str = ""
    success: bool


class StructuredAssistantContent(StrictResponseModel):
    """Structured assistant content extracted from one final response."""

    answer: str
    thinking: str | None = None
    suggestions: list[str] = []


class TurnError(StrictResponseModel):
    """User-safe terminal failure exposed by the HTTP API."""

    code: str
    message: str
    retryable: bool = False


class TurnOutcomeKind(StrEnum):
    """HTTP mirror of the typed turn outcome values."""

    FINAL = "final"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PauseReason(StrEnum):
    """Reason an HTTP chat response is paused."""

    CONFIRMATION = "confirmation"
    MISSING_INPUT = "missing_input"


class PendingInput(StrictResponseModel):
    """Typed missing value requested by the semantic planner."""

    pending_input_id: str
    question: str
    expected_input_schema: dict[str, Any]
    expires_at: str
    cas_version: int
    suggestions: list[str] = Field(default_factory=list, max_length=3)


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
    pause_reason: PauseReason | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    pending_input: PendingInput | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    used_tools: list[UsedToolItem] = []
    retrieved_memories: list[RetrievedDebugItem] | None = None
    retrieved_messages: list[RetrievedDebugItem] | None = None
    transcription: str | None = None
    stats: ExtendedUsage | None = None
    structured_content: StructuredAssistantContent | None = None
    stream_enabled: bool = False
    outcome_kind: TurnOutcomeKind | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    run_id: str = Field(default="", exclude_if=lambda value: not value)
    finalization_id: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    error: TurnError | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_typed_outcome(self) -> ChatResponse:
        """Mirror the strict IPC field combinations for typed outcomes."""
        if self.outcome_kind is None:
            return self
        if not self.run_id:
            raise ValueError("typed outcome requires run_id")
        if self.outcome_kind is TurnOutcomeKind.FINAL:
            if self.finalization_id is None or self.structured_content is None:
                raise ValueError(
                    "FINAL requires finalization_id and structured_content"
                )
            if (
                self.pending_confirmations
                or self.pending_input is not None
                or self.pause_reason is not None
                or self.error is not None
            ):
                raise ValueError("FINAL forbids confirmations and error")
        elif self.outcome_kind is TurnOutcomeKind.PAUSED:
            has_confirmations = bool(self.pending_confirmations)
            has_input = self.pending_input is not None
            if has_confirmations == has_input or self.pause_reason is None:
                raise ValueError("PAUSED requires exactly one typed pending form")
            if has_confirmations and self.pause_reason is not PauseReason.CONFIRMATION:
                raise ValueError("confirmation pause has the wrong reason")
            if has_input and self.pause_reason is not PauseReason.MISSING_INPUT:
                raise ValueError("missing-input pause has the wrong reason")
            if self.structured_content is not None or self.finalization_id is not None:
                raise ValueError("PAUSED forbids final content and finalization_id")
        elif self.outcome_kind is TurnOutcomeKind.FAILED:
            if self.error is None or self.finalization_id is None:
                raise ValueError("FAILED requires finalization_id and error")
            if self.pending_confirmations or self.pending_input is not None:
                raise ValueError("FAILED forbids pending state")
        elif (
            any(
                value is not None
                for value in (self.structured_content, self.finalization_id, self.error)
            )
            or self.pending_confirmations
            or self.pending_input is not None
            or self.pause_reason is not None
        ):
            raise ValueError("CANCELLED carries no final payload")
        return self


class ChatStartResponse(StrictResponseModel):
    """Chat start acknowledgement.

    The final assistant response is delivered by the stream `final` event.
    """

    conversation_id: str
    request_id: str
    run_id: str = Field(default="", exclude_if=lambda value: not value)
    stream_started: bool = True


class StopResponse(StrictResponseModel):
    """Stop endpoint response.

    Attributes:
        stopped: Whether an active loop was stopped.
    """

    stopped: bool
    run_id: str = Field(default="", exclude_if=lambda value: not value)


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

    show_thinking: bool = False
    show_suggestions: bool = True
    thinking_enabled: bool = True


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
    used_tools: list[UsedToolItem] = []
    pending_confirmations: list[ConfirmationItem] = []


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
