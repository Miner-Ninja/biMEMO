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

from pydantic import BaseModel, ConfigDict


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
    message: str
    channel: str
    session_id: str
    conversation_id: str
    images: list[str] = []
    attached_files: list[str] = []
    voice: str | None = None


class StopRequest(IPCModel):
    """Request from frontend to stop an in-progress chat.

    Attributes:
        request_id: Identifier of the chat request to stop.
    """

    request_id: str


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


class ReloadLLMRequest(IPCModel):
    """Request from frontend to reload the active LLM runtime configuration.

    Attributes:
        request_id: Unique identifier for this request.
    """

    request_id: str


# --- Orchestrator → Frontend messages ---


class StreamEventType(StrEnum):
    """Types of streaming events sent from orchestrator to frontend."""

    TOKEN = "token"
    THINKING = "thinking"
    TOOL_STATUS = "tool_status"
    CONFIRMATION = "confirmation"
    DONE = "done"
    MEMORY_EVENT = "memory_event"
    BRIDGE_INBOUND_MESSAGE = "bridge_inbound_message"
    BRIDGE_LINK_UPDATED = "bridge_link_updated"
    CONVERSATION_UPDATED = "conversation_updated"
    CONVERSATION_DELETED = "conversation_deleted"
    CONVERSATION_CREATED = "conversation_created"
    SCHEDULED_TASK_RESULT = "scheduled_task_result"


class StructuredAssistantContent(IPCModel):
    """Structured assistant content extracted from one final response."""

    answer: str
    thinking: str | None = None
    sql: str | None = None
    suggestions: list[str] = []


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
    structured_content: StructuredAssistantContent
    input_filtered: bool = False
    output_filtered: bool = False
    pending_confirmations: list[dict[str, Any]] | None = None
    retrieved_memories: list[dict[str, str]] | None = None
    retrieved_messages: list[dict[str, str]] | None = None
    transcription: str | None = None
    stats: ExtendedUsage | None = None
    stream_enabled: bool = False


class StopResponse(IPCModel):
    """Response confirming whether a chat was stopped.

    Attributes:
        request_id: Matching request identifier.
        stopped: Whether the chat was successfully stopped.
    """

    request_id: str
    stopped: bool


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
