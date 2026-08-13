"""IPC message models for multi-process LLM application architecture.

Defines Pydantic models for inter-process communication between
frontend (FastAPI), orchestrator, and database proxy processes.
All models use extra="forbid" to reject unknown fields at the
serialization boundary. Extends the existing ToolRequest/ToolResponse
models from protocol.py with chat, streaming, and database operation types.
"""

# ruff: noqa: S105

from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


if TYPE_CHECKING:
    from backend.models.chat_stats import ExtendedUsage


class IPCModel(BaseModel):
    """Base model for IPC payloads with strict field validation."""

    model_config = ConfigDict(extra="forbid")


# --- Frontend → Orchestrator messages ---


class ChatRequest(IPCModel):
    """Request from frontend to orchestrator to process a chat message.

    Attributes:
        request_id: Unique identifier for this request.
        message: User message text.
        channel: Communication channel (e.g., "web", "telegram").
        session_id: Session identifier for context.
        conversation_id: Conversation to append to.
    """

    request_id: str
    run_id: str = ""
    message: str
    channel: str
    session_id: str
    conversation_id: str
    images: list[str] = []
    attached_files: list[str] = []
    voice: str | None = None
    forced_tools: list[str] = []
    pending_input_id: str = ""
    pending_input_version: int | None = None
    pending_input_value: Any | None = None

    @model_validator(mode="after")
    def validate_pending_input_reply(self) -> ChatRequest:
        """Require a complete same-run identity for pending-input replies."""
        supplied = (
            bool(self.pending_input_id),
            self.pending_input_version is not None,
            self.pending_input_value is not None,
        )
        if any(supplied) and (not all(supplied) or not self.run_id):
            raise ValueError("pending input reply requires run, id, version, and value")
        return self


class StopRequest(IPCModel):
    """Request from frontend to stop an in-progress chat.

    Attributes:
        request_id: Identifier of the chat request to stop.
    """

    request_id: str
    run_id: str = ""


class ConfirmRequest(IPCModel):
    """Request from frontend to confirm or deny a pending action.

    Attributes:
        request_id: Identifier of the originating chat request.
        action_id: Identifier of the pending action.
        decision: User decision ("approve" or "deny").
    """

    request_id: str
    action_id: str
    decision: str


class BridgeInboundSyncRequest(IPCModel):
    """Request from frontend to persist one inbound bridge message.

    Attributes:
        request_id: Unique identifier for this request.
        provider: External messenger provider name.
        external_space_id: External server or provider scope identifier.
        external_channel_id: External channel identifier.
        external_message_id: External provider message identifier.
        author_id: External author identifier.
        author_name: External author display name.
        content: Plain text message content.
        images: Base64 data URLs of attached images.
    """

    request_id: str
    provider: str
    external_space_id: str
    external_channel_id: str
    external_message_id: str
    author_id: str
    author_name: str
    content: str
    images: list[str] = []


class McpMemoryRememberRequest(IPCModel):
    """Request from the MCP endpoint to store one external-agent memory.

    The write is executed in the orchestrator process (Socket A) so the
    web-facing FastAPI process never writes memory content directly.

    Attributes:
        request_id: Unique identifier for this request.
        content: Plain text content to store.
        channel: Provenance channel identifying the external agent source.
        agent_id: Stable identifier for the requesting agent (from bearer token).
    """

    request_id: str
    content: str
    channel: str
    agent_id: str


class MemoryInvalidateRequest(IPCModel):
    """Request from the admin UI to invalidate one graph assertion.

    The write is executed in the orchestrator process (Socket A) so the
    web-facing FastAPI process never writes memory content directly.

    Attributes:
        request_id: Unique identifier for this request.
        assertion_id: Assertion to invalidate.
        reason: User-stated reason ("forget" or "incorrect"), logged for
            diagnostics only.
    """

    request_id: str
    assertion_id: str
    reason: str


class McpAskRequest(IPCModel):
    """Request from the MCP endpoint to ask biMEMO a question.

    The orchestrator resolves the agent session, runs the normal
    agentic loop, and returns the answer. The web tier never writes
    memory content or calls outbound sends.

    Attributes:
        request_id: Unique identifier for this request.
        question: The question text to ask.
        agent_id: Stable identifier for the requesting agent.
    """

    request_id: str
    question: str
    agent_id: str


class ReloadLLMRequest(IPCModel):
    """Request from frontend to reload the active LLM runtime configuration.

    Attributes:
        request_id: Unique identifier for this request.
    """

    request_id: str


class ListAgentRunEventsRequest(IPCModel):
    """Request recent runtime journal events for a chat conversation or run."""

    request_id: str
    run_id: str = ""
    conversation_id: str = ""
    after_sequence: int = 0
    limit: int = 200


# --- Orchestrator → Frontend messages ---


class StreamEventType(StrEnum):
    """Types of streaming events sent from orchestrator to frontend."""

    TOKEN = "token"
    THINKING = "thinking"
    TOOL_STATUS = "tool_status"
    CONFIRMATION = "confirmation"
    FINAL = "final"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ERROR = "error"
    DONE = "done"
    MEMORY_EVENT = "memory_event"
    BRIDGE_INBOUND_MESSAGE = "bridge_inbound_message"
    BRIDGE_LINK_UPDATED = "bridge_link_updated"
    CONVERSATION_UPDATED = "conversation_updated"
    CONVERSATION_DELETED = "conversation_deleted"
    CONVERSATION_CREATED = "conversation_created"
    SCHEDULED_TASK_RESULT = "scheduled_task_result"
    MCP_ASK_RESULT = "mcp_ask_result"


class StructuredAssistantContent(IPCModel):
    """Structured assistant content extracted from one final response."""

    answer: str
    thinking: str | None = None
    suggestions: list[str] = []


class TurnOutcomeKind(StrEnum):
    """Closed typed result set for one chat turn."""

    FINAL = "final"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PauseReason(StrEnum):
    """Typed reason why a run is paused without a final answer."""

    CONFIRMATION = "confirmation"
    MISSING_INPUT = "missing_input"


class PendingInput(IPCModel):
    """One typed missing value required to resume a run."""

    pending_input_id: str
    question: str
    expected_input_schema: dict[str, Any]
    expires_at: str
    cas_version: int
    suggestions: list[str] = Field(default_factory=list, max_length=3)


class TurnError(IPCModel):
    """User-safe terminal failure transported over IPC."""

    code: str
    message: str
    retryable: bool = False


class ChatResponse(IPCModel):
    """Final response from orchestrator after completing the agentic loop.

    Attributes:
        request_id: Matching request identifier.
        structured_content: Assistant response split into visible answer and extras.
        input_filtered: Whether input content filtering was triggered.
        output_filtered: Whether output content filtering was triggered.
        pending_confirmations: List of actions awaiting user confirmation.
        retrieved_memories: Summaries of search-matched records.
        retrieved_messages: Debug metadata for FTS-matched messages.
    """

    request_id: str
    structured_content: StructuredAssistantContent | None = None
    input_filtered: bool = False
    output_filtered: bool = False
    pending_confirmations: list[dict[str, Any]] | None = None
    pause_reason: PauseReason | None = None
    pending_input: PendingInput | None = None
    used_tools: list[dict[str, Any]] | None = None
    retrieved_memories: list[dict[str, str]] | None = None
    retrieved_messages: list[dict[str, str]] | None = None
    transcription: str | None = None
    stats: ExtendedUsage | None = None
    stream_enabled: bool = False
    outcome_kind: TurnOutcomeKind | None = None
    run_id: str = ""
    finalization_id: str | None = None
    error: TurnError | None = None

    @model_validator(mode="after")
    def validate_typed_outcome(self) -> ChatResponse:
        """Reject incompatible fields when the new lifecycle is selected."""
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
                raise ValueError("FAILED requires finalization_id and typed error")
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


class StopResponse(IPCModel):
    """Response confirming whether a chat was stopped.

    Attributes:
        request_id: Matching request identifier.
        stopped: Whether the chat was successfully stopped.
    """

    request_id: str
    stopped: bool
    run_id: str = ""


class ConfirmResponse(IPCModel):
    """Response confirming a user decision was processed.

    Attributes:
        request_id: Matching request identifier.
        action_id: Identifier of the confirmed action.
        accepted: Whether the decision was accepted.
    """

    request_id: str
    action_id: str
    accepted: bool
    status: str = ""
    message_id: str = ""
    result_summary: str = ""


class BridgeInboundSyncResponse(IPCModel):
    """Response confirming one inbound bridge message was stored.

    Attributes:
        request_id: Matching request identifier.
        message_id: Stored internal biMEMO message identifier.
        synced: Whether the inbound message was accepted.
        assistant_content: LLM response text for the inbound message.
    """

    request_id: str
    conversation_id: str = ""
    message_id: str
    synced: bool
    reason: str = ""
    assistant_content: str = ""
    assistant_message_id: str = ""


class McpMemoryRememberResponse(IPCModel):
    """Response confirming one MCP external-agent memory was stored.

    Attributes:
        request_id: Matching request identifier.
        message_id: Stored internal biMEMO message identifier.
        conversation_id: Conversation the message was stored under.
        stored: Whether the message was persisted.
        reason: Explanation when stored=false (e.g. "duplicate",
            "rate_limited", "oversized").
    """

    request_id: str
    message_id: str = ""
    conversation_id: str = ""
    stored: bool = False
    reason: str = ""


class MemoryInvalidateResponse(IPCModel):
    """Response confirming one assertion-invalidation attempt.

    Attributes:
        request_id: Matching request identifier.
        invalidated: True when the assertion was active and is now
            invalidated; False for unknown or already-invalid ids.
    """

    request_id: str
    invalidated: bool = False


class McpAskResponse(IPCModel):
    """Response from biMEMO after processing an agent's question.

    Attributes:
        request_id: Matching request identifier.
        answer: biMEMO's answer text.
        conversation_id: The agent's session conversation.
        session_id: The agent's session identifier.
        error: Error message when the request failed.
    """

    request_id: str
    answer: str = ""
    conversation_id: str = ""
    session_id: str = ""
    finalization_id: str = ""
    error: str = ""


class ReloadLLMResponse(IPCModel):
    """Response describing the result of one LLM hot-reload attempt.

    Attributes:
        request_id: Matching request identifier.
        success: Whether the reload completed successfully.
        active_provider: Newly active provider name, when available.
        model: Newly active model name, when available.
        error: Short error summary on failure.
    """

    request_id: str
    success: bool
    active_provider: str | None = None
    model: str | None = None
    error: str | None = None


class ListAgentRunEventsResponse(IPCModel):
    """Response containing runtime journal events for reconnect recovery."""

    request_id: str
    events: list[dict[str, Any]] = []


class StreamEvent(IPCModel):
    """Streaming event from orchestrator to frontend.

    Carries incremental updates (tokens, tool status, confirmations)
    during agentic loop execution. Correlated to the originating
    request via request_id.

    Attributes:
        request_id: Matching request identifier.
        event_type: Type of streaming event.
        data: Event-specific payload.
    """

    request_id: str
    event_type: StreamEventType
    data: dict[str, Any] | str


# --- Named database operation messages ---


class DBOperation(IPCModel):
    """Request to execute a named database operation via DB Proxy.

    Attributes:
        request_id: Unique identifier for this operation.
        operation: Named operation (e.g., "save_message", "load_context").
        parameters: Operation-specific parameters.
    """

    request_id: str
    operation: str
    parameters: dict[str, Any] | None = None


class DBResult(IPCModel):
    """Result of a database operation from DB Proxy.

    Attributes:
        request_id: Matching request identifier.
        status: Outcome ("success" or "error").
        data: Operation result data (on success).
        error_message: Error description (on failure).
    """

    request_id: str
    status: str
    data: dict[str, Any] | None = None
    error_message: str | None = None


# --- Multimodal content helpers ---


def _text_content_part(message: str) -> dict[str, Any]:
    """Build one text part for a multimodal message."""
    return {"type": "text", "text": message}


def _image_url_content_part(url: str) -> dict[str, Any]:
    """Build one image-url part for a multimodal message."""
    return {"type": "image_url", "image_url": {"url": url}}


def _content_part_text(part: dict[str, Any]) -> str:
    """Extract the visible text for one multimodal content part."""
    if part.get("type") == "text":
        return str(part.get("text", ""))
    if part.get("type") == "image_url":
        return "[image]"
    return ""


def build_multimodal_content(
    message: str, images: list[str]
) -> str | list[dict[str, Any]]:
    """Build OpenAI-compatible content field.

    Returns plain string when no images, content array when images present.
    """
    if not images:
        return message
    parts: list[dict[str, Any]] = [_text_content_part(message)]
    for url in images:
        parts.append(_image_url_content_part(url))
    return parts


def extract_image_urls(content: str | list) -> list[str]:
    """Extract image data URLs from multimodal content."""
    if isinstance(content, str):
        return []
    urls: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url:
                urls.append(url)
    return urls


def extract_text_content(content: str | list) -> str:
    """Extract text from content, replacing images with [image] placeholder."""
    if isinstance(content, str):
        return content
    text_parts: list[str] = []
    for part in content:
        if isinstance(part, dict):
            visible_text = _content_part_text(part)
            if visible_text:
                text_parts.append(visible_text)
    return " ".join(text_parts) if text_parts else ""


ChatResponse.model_rebuild(
    _types_namespace={
        "ExtendedUsage": import_module("backend.models.chat_stats").ExtendedUsage
    }
)
