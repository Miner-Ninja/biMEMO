"""Chat request handling for the orchestrator process.

Handles incoming chat requests: voice transcription, SSE event forwarding,
conversation processing state management, and message preparation. Bridges
the IPC layer with the gateway orchestrator for individual chat exchanges.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx

from backend.ipc.ipc_channel import IPCChannelError
from backend.ipc.models import (
    ChatRequest,
    ChatResponse,
    StreamEvent,
    StreamEventType,
    StructuredAssistantContent,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.agent.agent_loop_models import AgentResponse

    from .client import DBProxyClient
    from .ipc_server import OrchestratorIPCServer


logger = logging.getLogger(__name__)

RECOVERABLE_TRANSCRIPTION_ERRORS = (
    RuntimeError,
    ValueError,
    OSError,
    httpx.HTTPError,
)
RECOVERABLE_AUDIT_ERRORS = (RuntimeError, IPCChannelError, TypeError, ValueError)
DEFAULT_CONVERSATION_PROCESSING_STAGE = "Processing..."


class VoiceTranscriptionError(RuntimeError):
    """Raised when an incoming voice payload cannot be transcribed."""


async def _forward_request_events_to_ipc(
    *,
    request_id: str,
    request_sse_queue: asyncio.Queue,
    ipc_server: OrchestratorIPCServer,
) -> None:
    """Forward per-request SSE events from the orchestrator queue into IPC."""
    while True:
        event_data = await request_sse_queue.get()
        event_type_value = event_data.get("type")
        if event_type_value == "token":
            event_type = StreamEventType.TOKEN
        elif event_type_value == "thinking":
            event_type = StreamEventType.THINKING
        elif event_type_value == "tool_status":
            event_type = StreamEventType.TOOL_STATUS
        elif event_type_value == "done":
            event_type = StreamEventType.DONE
        else:
            logger.debug(
                "Skipping unsupported SSE event type %r",
                event_type_value,
            )
            continue
        await ipc_server.push_event(
            StreamEvent(
                request_id=request_id,
                event_type=event_type,
                data=event_data,
            )
        )
        if event_type is StreamEventType.DONE:
            break


async def _transcribe_voice_request(
    *,
    request: ChatRequest,
    stt_client: object,
    broker: object,
) -> str:
    """Transcribe one voice payload and return its text form."""
    import base64 as b64mod

    header, data_part = request.voice.split(",", 1)
    mime_type = header.split(":")[1].split(";")[0]
    audio_bytes = b64mod.b64decode(data_part)
    transcription = await stt_client.transcribe(
        audio_bytes,
        mime_type,
    )
    logger.info("Voice transcribed: %d chars", len(transcription))
    try:
        await broker.audit_fn(
            event_type="voice_transcription",
            tool_name="stt",
            parameters=json.dumps(
                {
                    "channel": request.channel,
                    "audio_size_bytes": len(audio_bytes),
                }
            ),
            result=json.dumps({"text_length": len(transcription)}),
        )
    except RECOVERABLE_AUDIT_ERRORS:
        logger.debug(
            "Failed to audit voice transcription",
            exc_info=True,
        )
    return transcription


def _should_transcribe_voice_request(
    request: ChatRequest,
    stt_client: object | None,
) -> bool:
    """Return whether this request should go through voice transcription."""
    return bool(request.voice and stt_client is not None)


def _merge_transcription_with_message(
    *,
    transcription: str,
    message: str,
) -> str:
    """Merge the voice transcription with any existing typed message text."""
    if message.strip():
        return f"{transcription}\n{message}"
    return transcription


async def _prepare_request_message(
    *,
    request: ChatRequest,
    stt_client: object | None,
    broker: object,
) -> tuple[str, str | None]:
    """Prepare the final input text and optional transcription for one request."""
    if not _should_transcribe_voice_request(request, stt_client):
        return request.message, None

    try:
        transcription = await _transcribe_voice_request(
            request=request,
            stt_client=stt_client,
            broker=broker,
        )
    except RECOVERABLE_TRANSCRIPTION_ERRORS as error:
        raise VoiceTranscriptionError from error

    return _merge_transcription_with_message(
        transcription=transcription,
        message=request.message,
    ), transcription


def _build_chat_response(
    *,
    request_id: str,
    response: AgentResponse,
    transcription: str | None,
) -> ChatResponse:
    """Convert one gateway response into the IPC chat response shape."""
    return ChatResponse(
        request_id=request_id,
        structured_content=_copy_structured_content(response.structured_content),
        input_filtered=response.input_filtered,
        output_filtered=response.output_filtered,
        retrieved_memories=response.retrieved_memories or None,
        retrieved_messages=response.retrieved_messages or None,
        transcription=transcription,
        stats=response.stats,
    )


def _copy_structured_content(
    structured_content: StructuredAssistantContent,
) -> StructuredAssistantContent:
    """Copy one structured assistant reply into the IPC model."""
    return StructuredAssistantContent(
        answer=structured_content.answer,
        thinking=structured_content.thinking,
        sql=structured_content.sql,
        suggestions=structured_content.suggestions,
    )


def _build_plain_structured_content(answer: str) -> StructuredAssistantContent:
    """Build one minimal structured assistant reply from visible text only."""
    return StructuredAssistantContent(answer=answer)


def _build_missing_llm_response(
    request_id: str,
) -> ChatResponse:
    """Build the standard response for a request without a configured LLM."""
    visible_answer = (
        "No LLM provider configured. "
        "Please go to Admin → LLM Configuration and save "
        "your provider settings, "
        "then restart the application."
    )
    return ChatResponse(
        request_id=request_id,
        structured_content=_build_plain_structured_content(visible_answer),
        input_filtered=False,
        output_filtered=False,
    )


def _build_voice_transcription_failure_response(
    request_id: str,
) -> ChatResponse:
    """Build the standard response for a failed voice transcription."""
    visible_answer = (
        "Voice transcription failed. "
        "Please try again or send a text message."
    )
    return ChatResponse(
        request_id=request_id,
        structured_content=_build_plain_structured_content(visible_answer),
        input_filtered=False,
        output_filtered=False,
    )


def _resolve_request_processing_stage(request: ChatRequest) -> str:
    """Choose the visible processing stage for one chat request."""
    if request.voice:
        return "Transcribing..."
    return DEFAULT_CONVERSATION_PROCESSING_STAGE


async def _mark_conversation_processing_active(
    *,
    db_client: DBProxyClient,
    conversation_id: str,
    processing_stage: str,
) -> None:
    """Mark one conversation as actively processing."""
    from .process_helpers import current_processing_timestamp

    await db_client.update_conversation_processing_state(
        conversation_id,
        "active",
        processing_stage=processing_stage,
        processing_started_at=current_processing_timestamp(),
    )


async def _mark_conversation_processing_idle(
    *,
    db_client: DBProxyClient,
    conversation_id: str,
) -> None:
    """Clear the processing state for one conversation."""
    await db_client.update_conversation_processing_state(
        conversation_id,
        "idle",
        processing_stage="",
        processing_started_at=None,
    )


async def _run_with_processing_state[T](
    *,
    db_client: DBProxyClient,
    conversation_id: str,
    processing_stage: str,
    runner: Callable[[], Awaitable[T]],
) -> T:
    """Run one coroutine while the conversation is marked as processing."""
    await _mark_conversation_processing_active(
        db_client=db_client,
        conversation_id=conversation_id,
        processing_stage=processing_stage,
    )
    try:
        return await runner()
    finally:
        await _mark_conversation_processing_idle(
            db_client=db_client,
            conversation_id=conversation_id,
        )


async def _finish_request_event_forwarding(
    *,
    request_sse_queue: asyncio.Queue,
    event_forwarder: asyncio.Task,
    orch: object,
) -> None:
    """Finish SSE forwarding for one chat request and clear the runtime queue."""
    await request_sse_queue.put({"type": "done"})
    await event_forwarder
    orch.sse_queue = None


async def _run_prepared_chat_request(
    *,
    request: ChatRequest,
    orch: object,
    llm_call: Callable,
    message: str,
    transcription: str | None,
) -> ChatResponse:
    """Run one already-prepared chat message through the orchestrator."""
    request_meta = {"source": "voice"} if transcription else None
    response = await _run_orchestrator_message(
        orch=orch,
        message=message,
        channel=request.channel,
        llm_call=llm_call,
        session_id=request.session_id,
        conversation_id=request.conversation_id,
        images=request.images,
        attached_files=request.attached_files or None,
        meta=request_meta,
    )
    return _build_chat_response(
        request_id=request.request_id,
        response=response,
        transcription=transcription,
    )


async def _run_chat_request(
    *,
    request: ChatRequest,
    orch: object,
    ipc_server: OrchestratorIPCServer,
    db_client: DBProxyClient,
    broker: object,
    llm_call: Callable,
    stt_client: object | None,
) -> ChatResponse:
    """Run one chat request through the orchestrator and IPC event bridge."""
    request_sse_queue, event_forwarder = _start_request_event_forwarding(
        request_id=request.request_id,
        ipc_server=ipc_server,
        orch=orch,
    )
    try:
        return await _run_with_processing_state(
            db_client=db_client,
            conversation_id=request.conversation_id,
            processing_stage=_resolve_request_processing_stage(request),
            runner=lambda: _run_chat_request_with_prepared_message(
                request=request,
                orch=orch,
                broker=broker,
                llm_call=llm_call,
                stt_client=stt_client,
            ),
        )
    finally:
        await _finish_request_event_forwarding(
            request_sse_queue=request_sse_queue,
            event_forwarder=event_forwarder,
            orch=orch,
        )


async def _run_chat_request_with_prepared_message(
    *,
    request: ChatRequest,
    orch: object,
    broker: object,
    llm_call: Callable,
    stt_client: object | None,
) -> ChatResponse:
    """Prepare one chat request message and run it through the orchestrator."""
    try:
        message, transcription = await _prepare_request_message(
            request=request,
            stt_client=stt_client,
            broker=broker,
        )
    except VoiceTranscriptionError:
        logger.error("Voice transcription failed", exc_info=True)
        return _build_voice_transcription_failure_response(
            request.request_id,
        )
    return await _run_prepared_chat_request(
        request=request,
        orch=orch,
        llm_call=llm_call,
        message=message,
        transcription=transcription,
    )


def _start_request_event_forwarding(
    *,
    request_id: str,
    ipc_server: OrchestratorIPCServer,
    orch: object,
) -> tuple[asyncio.Queue, asyncio.Task]:
    """Create the per-request queue, start forwarding, and bind it to the runtime."""
    request_sse_queue: asyncio.Queue = asyncio.Queue()
    event_forwarder = asyncio.create_task(
        _forward_request_events_to_ipc(
            request_id=request_id,
            request_sse_queue=request_sse_queue,
            ipc_server=ipc_server,
        )
    )
    orch.sse_queue = request_sse_queue
    return request_sse_queue, event_forwarder


async def _run_orchestrator_message(
    *,
    orch: object,
    message: str,
    channel: str,
    llm_call: Callable,
    session_id: str,
    conversation_id: str,
    images: list[str] | None = None,
    attached_files: list[str] | None = None,
    meta: dict | None = None,
) -> AgentResponse:
    """Run one message through the shared orchestrator chat path."""
    return await orch.process_message(
        message=message,
        channel=channel,
        llm_call=llm_call,
        session_id=session_id,
        conversation_id=conversation_id,
        images=images,
        attached_files=attached_files,
        meta=meta,
    )


def _build_orchestrator_request_handler(
    *,
    orch: object,
    ipc_server_ref: Callable[[], OrchestratorIPCServer],
    db_client: DBProxyClient,
    broker: object,
    get_llm_call: Callable[[], Callable | None],
    stt_client: object | None,
) -> Callable[[ChatRequest], Awaitable[ChatResponse]]:
    """Build the handler used by FastAPI to send chat work into the process."""

    async def orchestrator_fn(request: ChatRequest) -> ChatResponse:
        """Run one chat request through the orchestrator process entrypoint."""
        llm_call = get_llm_call()
        if llm_call is None:
            return _build_missing_llm_response(request.request_id)
        return await _run_chat_request(
            request=request,
            orch=orch,
            ipc_server=ipc_server_ref(),
            db_client=db_client,
            broker=broker,
            llm_call=llm_call,
            stt_client=stt_client,
        )

    return orchestrator_fn
