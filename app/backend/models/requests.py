"""Pydantic request models for all API endpoints.

Defines strict request schemas with validation. Extra fields
are forbidden to prevent unexpected data from reaching handlers.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictRequestModel(BaseModel):
    """Base request model with strict field validation."""

    model_config = ConfigDict(extra="forbid")


def _deduplicated_uuid_strings(values: list[str]) -> list[str]:
    """Normalize UUID strings and keep only the first occurrence of each one."""
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        parsed = str(UUID(value))
        if parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


class ChatRequest(StrictRequestModel):
    """Chat message request.

    Attributes:
        message: User message text.
        conversation_id: Stable conversation identifier for episodic memory.
        images: Optional list of base64 data URLs for image attachments.
        file_ids: Optional list of active file IDs for context.
    """

    message: str = Field(default="", max_length=100_000)
    conversation_id: str = Field(default="", max_length=200)
    images: list[str] = Field(default_factory=list, max_length=4)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    voice: str | None = None
    forced_tools: list[str] = Field(default_factory=list, max_length=32)
    run_id: str = Field(default="", max_length=200)
    pending_input_id: str = Field(default="", max_length=200)
    pending_input_version: int | None = Field(default=None, ge=1)
    pending_input_value: Any | None = None

    @field_validator("file_ids")
    @classmethod
    def _validate_file_ids(cls, value: list[str]) -> list[str]:
        """Normalize and deduplicate attached file identifiers."""
        return _deduplicated_uuid_strings(value)

    @model_validator(mode="after")
    def _require_message_or_voice(self) -> ChatRequest:
        """Require at least one chat input source."""
        if not self.message.strip() and not self.voice and not self.forced_tools:
            raise ValueError("Either message or voice must be provided")
        supplied = (
            bool(self.pending_input_id),
            self.pending_input_version is not None,
            self.pending_input_value is not None,
        )
        if any(supplied) and (not all(supplied) or not self.run_id):
            raise ValueError("pending input reply requires run, id, version, and value")
        return self


class UiSettingsRequest(StrictRequestModel):
    """Web-tier UI settings blob replacement request."""

    settings: dict[str, Any]


class ConfirmRequest(StrictRequestModel):
    """Trust confirmation decision request.

    Attributes:
        action_id: Cryptographic action identifier.
        decision: Either "approve" or "deny".
    """

    action_id: str = Field(min_length=1)
    decision: str = Field(pattern=r"^(approve|deny)$")


class StopRequest(StrictRequestModel):
    """Stop an in-progress chat request."""

    request_id: str = Field(default="", max_length=200)
    run_id: str = Field(default="", max_length=200)


class IntegrationCreateRequest(StrictRequestModel):
    """Add integration request.

    Attributes:
        name: Integration name (unique identifier).
        type: Integration type (e.g., "email", "telegram").
        config: Integration-specific configuration.
    """

    name: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)


class ChatBridgeCreateRequest(StrictRequestModel):
    """Create external chat bridge request.

    Attributes:
        conversation_id: Existing web conversation identifier.
        provider: External messenger provider name.
        sync_direction: Allowed bridge synchronization direction.
        delivery_mode: How messages should be presented on the external side.
        persona_mode: How user persona should be resolved for webhook delivery.
        persona_user_id: Explicit provider-side user identifier for mapped persona mode.
        title: Optional external room title override.
    """

    conversation_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=50)
    sync_direction: str = Field(
        default="bidirectional",
        pattern=r"^(web_to_external|external_to_web|bidirectional)$",
    )
    delivery_mode: str = Field(
        default="bot_only",
        pattern=r"^(bot_only|persona_user_and_bot_assistant)$",
    )
    persona_mode: str = Field(
        default="neutral",
        pattern=r"^(neutral|mapped_user)$",
    )
    persona_user_id: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _validate_persona_mapping(self) -> ChatBridgeCreateRequest:
        """Require a provider user id when persona mapping is enabled."""
        if self.persona_mode == "mapped_user" and not self.persona_user_id.strip():
            raise ValueError(
                "persona_user_id is required when persona_mode is mapped_user"
            )
        return self


class ChatBridgeUpdateRequest(StrictRequestModel):
    """Update stored external chat bridge settings."""

    delivery_mode: str = Field(
        default="bot_only",
        pattern=r"^(bot_only|persona_user_and_bot_assistant)$",
    )
    persona_mode: str = Field(
        default="neutral",
        pattern=r"^(neutral|mapped_user)$",
    )
    persona_user_id: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _validate_persona_mapping(self) -> ChatBridgeUpdateRequest:
        """Require a provider user id when persona mapping is enabled."""
        if self.persona_mode == "mapped_user" and not self.persona_user_id.strip():
            raise ValueError(
                "persona_user_id is required when persona_mode is mapped_user"
            )
        return self


class ConversationCreateRequest(StrictRequestModel):
    """Create one new persisted conversation."""

    title: str = Field(default="", max_length=200)


class ConversationUpdateRequest(StrictRequestModel):
    """Update one persisted conversation."""

    title: str = Field(default="", max_length=200)


class ChatSettingsUpdateRequest(StrictRequestModel):
    """Update per-conversation display settings."""

    show_thinking: bool | None = None
    show_suggestions: bool | None = None
    thinking_enabled: bool | None = None


class ChatBridgeInboundMessageRequest(StrictRequestModel):
    """Inbound external bridge message request.

    Attributes:
        external_space_id: External server or parent scope identifier.
        external_channel_id: External channel identifier.
        external_message_id: Provider-side message identifier.
        author_id: External author identifier.
        author_name: External author display name.
        content: Plain text message content.
        images: Base64 data URLs of attached images.
    """

    external_space_id: str = Field(min_length=1, max_length=200)
    external_channel_id: str = Field(min_length=1, max_length=200)
    external_message_id: str = Field(min_length=1, max_length=200)
    author_id: str = Field(min_length=1, max_length=200)
    author_name: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=100_000, default="")
    images: list[str] = Field(default_factory=list)


class ReauthRequest(StrictRequestModel):
    """Re-authentication request for sensitive operations.

    Attributes:
        password: Unlock password for verification.
    """

    password: str = Field(min_length=1)


class PasswordAuthRequest(StrictRequestModel):
    """Password-based auth request for login/setup flows."""

    password: str = Field(min_length=8)


class UserProfileRequest(StrictRequestModel):
    """User profile update payload."""

    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    display_name: str = Field(default="", max_length=200)
    assistant_name: str = Field(default="", max_length=200)
    timezone: str = Field(default="", max_length=100)
    language: str = Field(default="", max_length=50)
    custom_instructions: str = Field(default="", max_length=2000)


class ScheduledTaskCreateRequest(StrictRequestModel):
    """Create-task payload for the scheduler admin endpoint."""

    name: str = Field(min_length=1, max_length=200)
    task_type: str = Field(pattern="^(reminder|agent_task)$")
    prompt: str = Field(min_length=1, max_length=10000)
    schedule: str = Field(min_length=1, max_length=200)
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    repeat_limit: int | None = Field(default=None, ge=1)
    conversation_id: str = Field(default="", max_length=200)


class ScheduledTaskUpdateRequest(StrictRequestModel):
    """Update-task payload for the scheduler admin endpoint.

    All fields are optional — only fields present in the body are updated.
    """

    status: str | None = Field(default=None, pattern="^(active|paused|completed)$")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, min_length=1, max_length=10000)
    schedule: str | None = Field(default=None, min_length=1, max_length=200)
    allowed_tools: list[str] | None = Field(default=None, max_length=50)
    repeat_limit: int | None = Field(default=None, ge=1)
