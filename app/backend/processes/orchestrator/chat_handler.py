"""Chat request handling for the orchestrator process.

Handles incoming chat requests: voice transcription, stream event forwarding,
conversation processing state management, and message preparation. Bridges
the IPC layer with the gateway orchestrator for individual chat exchanges.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx

from backend.agent.conversation_settings import (
    read_suggestions_enabled,
    read_thinking_enabled,
)
from backend.config import get_settings
from backend.ipc.ipc_channel import IPCChannelError
from backend.ipc.models import (
    ChatRequest,
    ChatResponse,
    StreamEvent,
    StreamEventType,
    StructuredAssistantContent,
)
from backend.llm.call_factory import set_thinking_enabled
from backend.processes.orchestrator.run_event_recorder import (
    AgentRunEventType,
    RunEventContext,
    RunEventRecorder,
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

STREAM_EVENT_TYPE_BY_VALUE = {
    "token": StreamEventType.TOKEN,
    "thinking": StreamEventType.THINKING,
    "tool_status": StreamEventType.TOOL_STATUS,
    "confirmation": StreamEventType.CONFIRMATION,
    "final": StreamEventType.FINAL,
    "error": StreamEventType.ERROR,
    "conversation_updated": StreamEventType.CONVERSATION_UPDATED,
    "done": StreamEventType.DONE,
}


class VoiceTranscriptionError(RuntimeError):
    """Raised when an incoming voice payload cannot be transcribed."""


async def _forward_request_events_to_ipc(
    *,
    request_id: str,
    request_stream_queue: asyncio.Queue,
    ipc_server: OrchestratorIPCServer,
    run_event_recorder: RunEventRecorder | None = None,
) -> None:
    """Forward per-request stream events from the orchestrator queue into IPC."""
    while True:
        event_data = await request_stream_queue.get()
        event_type = _stream_event_type_from_payload(event_data)
        if event_type is None:
            logger.debug(
                "Skipping unsupported stream event type %r",
                event_data.get("type"),
            )
            continue
        payload = _ipc_payload_from_stream_payload(event_data, event_type)
        await ipc_server.push_event(
            StreamEvent(
                request_id=request_id,
                event_type=event_type,
                data=payload,
            )
        )
        if run_event_recorder is not None:
            await run_event_recorder.record_stream_event(payload)
        if event_type is StreamEventType.DONE:
            break


def _stream_event_type_from_payload(
    event_data: dict[str, object],
) -> StreamEventType | None:
    """Resolve one internal stream payload to its IPC event type."""
    return STREAM_EVENT_TYPE_BY_VALUE.get(str(event_data.get("type", "")))


def _ipc_payload_from_stream_payload(
    event_data: dict[str, object],
    event_type: StreamEventType,
) -> dict[str, object]:
    """Return the browser-facing IPC payload for one internal stream event."""
    if event_type is StreamEventType.CONVERSATION_UPDATED:
        data = event_data.get("data", {})
        return data if isinstance(data, dict) else {}
    return event_data


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
    pending_confirmations: list[dict[str, object]] | None = None,
    used_tools: list[dict[str, object]] | None = None,
) -> ChatResponse:
    """Convert one gateway response into the IPC chat response shape."""
    return ChatResponse(
        request_id=request_id,
        structured_content=_copy_structured_content(response.structured_content),
        input_filtered=response.input_filtered,
        output_filtered=response.output_filtered,
        retrieved_memories=response.retrieved_memories or None,
        retrieved_messages=response.retrieved_messages or None,
        pending_confirmations=pending_confirmations,
        used_tools=used_tools,
        transcription=transcription,
        stats=response.stats,
        stream_enabled=get_settings().llm_response_stream,
    )


def _copy_structured_content(
    structured_content: StructuredAssistantContent,
) -> StructuredAssistantContent:
    """Copy one structured assistant reply into the IPC model."""
    return StructuredAssistantContent(
        answer=structured_content.answer,
        thinking=structured_content.thinking,
        suggestions=structured_content.suggestions,
    )


def _build_plain_structured_content(
    response_text: str,
) -> StructuredAssistantContent:
    """Build one minimal structured assistant reply from visible text only."""
    return StructuredAssistantContent(answer=response_text)


def _build_missing_llm_response(
    request_id: str,
) -> ChatResponse:
    """Build the standard response for a request without a configured LLM."""
    visible_answer = (
        "No LLM provider configured. "
        "Please go to Admin → LLM Configuration → add "
        "and activate your provider settings."
    )
    return ChatResponse(
        request_id=request_id,
        structured_content=_build_plain_structured_content(visible_answer),
        input_filtered=False,
        output_filtered=False,
        stream_enabled=get_settings().llm_response_stream,
    )


def _build_voice_transcription_failure_response(
    request_id: str,
) -> ChatResponse:
    """Build the standard response for a failed voice transcription."""
    visible_answer = (
        "Voice transcription failed. Please try again or send a text message."
    )
    return ChatResponse(
        request_id=request_id,
        structured_content=_build_plain_structured_content(visible_answer),
        input_filtered=False,
        output_filtered=False,
        stream_enabled=get_settings().llm_response_stream,
    )


def _chat_response_final_event_data(
    *,
    conversation_id: str,
    response: ChatResponse,
) -> dict[str, object]:
    """Build one stream-final payload from the IPC chat response."""
    return {
        "type": "final",
        "conversation_id": conversation_id,
        "input_filtered": response.input_filtered,
        "output_filtered": response.output_filtered,
        "pending_confirmations": response.pending_confirmations or [],
        "retrieved_memories": response.retrieved_memories,
        "retrieved_messages": response.retrieved_messages,
        "transcription": response.transcription,
        "stats": response.stats.model_dump(mode="json")
        if response.stats is not None
        else None,
        "structured_content": response.structured_content.model_dump(
            mode="json",
        ),
        "stream_enabled": response.stream_enabled,
    }


def _chat_error_event_data(
    *,
    conversation_id: str,
    message: str,
) -> dict[str, object]:
    """Build one stream-error payload for a failed request."""
    return {
        "type": "error",
        "conversation_id": conversation_id,
        "message": message,
    }


def _conversation_updated_event_data(
    *,
    conversation_id: str,
    processing_status: str,
    processing_stage: str,
    processing_started_at: str | None,
) -> dict[str, object]:
    """Build one conversation processing-state stream payload."""
    return {
        "type": "conversation_updated",
        "data": {
            "conversation_id": conversation_id,
            "processing_status": processing_status,
            "processing_stage": processing_stage,
            "processing_started_at": processing_started_at,
        },
    }


def _done_event_data() -> dict[str, object]:
    """Build one terminal stream completion payload."""
    return {"type": "done"}


async def _publish_final_chat_event(
    *,
    request_stream_queue: asyncio.Queue,
    conversation_id: str,
    response: ChatResponse,
) -> None:
    """Publish the authoritative final assistant payload for one request."""
    await request_stream_queue.put(
        _chat_response_final_event_data(
            conversation_id=conversation_id,
            response=response,
        )
    )


async def _publish_chat_error_event(
    *,
    request_stream_queue: asyncio.Queue,
    conversation_id: str,
    message: str,
) -> None:
    """Publish one terminal stream error for a failed started request."""
    await request_stream_queue.put(
        _chat_error_event_data(
            conversation_id=conversation_id,
            message=message,
        )
    )


def _resolve_request_processing_stage(request: ChatRequest) -> str:
    """Choose the visible processing stage for one chat request."""
    if request.voice:
        return "Transcribing..."
    return DEFAULT_CONVERSATION_PROCESSING_STAGE


async def _push_conversation_updated_event(
    *,
    orch: object,
    conversation_id: str,
    processing_status: str,
    processing_stage: str,
    processing_started_at: str | None,
) -> None:
    """Push a conversation_updated event onto the bound request stream queue."""
    sse_queue = getattr(orch, "sse_queue", None)
    if sse_queue is None:
        return
    await sse_queue.put(
        _conversation_updated_event_data(
            conversation_id=conversation_id,
            processing_status=processing_status,
            processing_stage=processing_stage,
            processing_started_at=processing_started_at,
        )
    )


async def _mark_conversation_processing_active(
    *,
    db_client: DBProxyClient,
    orch: object,
    conversation_id: str,
    processing_stage: str,
) -> None:
    """Mark one conversation as actively processing."""
    from .process_helpers import current_processing_timestamp

    started_at = current_processing_timestamp()
    await db_client.update_conversation_processing_state(
        conversation_id,
        "active",
        processing_stage=processing_stage,
        processing_started_at=started_at,
    )
    await _push_conversation_updated_event(
        orch=orch,
        conversation_id=conversation_id,
        processing_status="active",
        processing_stage=processing_stage,
        processing_started_at=started_at,
    )


async def _mark_conversation_processing_idle(
    *,
    db_client: DBProxyClient,
    orch: object,
    conversation_id: str,
) -> None:
    """Clear the processing state for one conversation."""
    await db_client.update_conversation_processing_state(
        conversation_id,
        "idle",
        processing_stage="",
        processing_started_at=None,
    )
    await _push_conversation_updated_event(
        orch=orch,
        conversation_id=conversation_id,
        processing_status="idle",
        processing_stage="",
        processing_started_at=None,
    )


async def _run_with_processing_state[T](
    *,
    db_client: DBProxyClient,
    orch: object,
    conversation_id: str,
    processing_stage: str,
    runner: Callable[[], Awaitable[T]],
) -> T:
    """Run one coroutine while the conversation is marked as processing."""
    await _mark_conversation_processing_active(
        db_client=db_client,
        orch=orch,
        conversation_id=conversation_id,
        processing_stage=processing_stage,
    )
    try:
        return await runner()
    finally:
        await _mark_conversation_processing_idle(
            db_client=db_client,
            orch=orch,
            conversation_id=conversation_id,
        )


async def _finish_request_event_forwarding(
    *,
    request_stream_queue: asyncio.Queue,
    event_forwarder: asyncio.Task,
    orch: object,
) -> None:
    """Finish stream forwarding for one chat request and clear the runtime queue."""
    await request_stream_queue.put(_done_event_data())
    await event_forwarder
    orch.sse_queue = None


async def _run_prepared_chat_request(
    *,
    request: ChatRequest,
    orch: object,
    llm_call: Callable,
    message: str,
    transcription: str | None,
    agent_run_id: str = "",
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
        forced_tools=request.forced_tools,
        attached_files=request.attached_files or None,
        meta=request_meta,
        agent_run_id=agent_run_id,
    )
    return _build_chat_response(
        request_id=request.request_id,
        response=response,
        transcription=transcription,
        pending_confirmations=_pending_confirmations_from_orchestrator(orch),
        used_tools=_used_tools_from_orchestrator(orch),
    )


def _pending_confirmations_from_orchestrator(
    orch: object,
) -> list[dict[str, object]] | None:
    """Return pending confirmation payloads for the HTTP response."""
    pending = getattr(orch, "pending_red_calls", [])
    if not pending:
        return None
    return [
        {
            "action_id": action.action_id,
            "tool_name": action.tool_name,
            "parameters": action.parameters,
        }
        for action in pending
    ]


def _used_tools_from_orchestrator(
    orch: object,
) -> list[dict[str, object]] | None:
    """Return executed tool payloads for the HTTP response."""
    from backend.security.policy import get_policy

    if not get_policy().get_bool("tools.manual_control_enabled"):
        return None
    tools = getattr(orch, "used_tools", [])
    if not tools:
        return None
    return list(tools)


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
    run_event_context = RunEventContext(
        request_id=request.request_id,
        conversation_id=request.conversation_id,
    )
    run_event_recorder = RunEventRecorder(
        db_client=db_client,
        context=run_event_context,
    )
    request_stream_queue, event_forwarder = _start_request_event_forwarding(
        request_id=request.request_id,
        ipc_server=ipc_server,
        orch=orch,
        run_event_recorder=run_event_recorder,
    )
    # Suggestions are on by default in interactive chats; the per-chat toggle
    # can turn them off. Set before the turn (the prompt is built inside) and
    # restored afterwards since the orchestrator is reused across turns.
    orch.prompt_builder.suggestions_enabled = await read_suggestions_enabled(
        db_client, request.conversation_id, default=True
    )
    # Same pattern for the reasoning switch: read the per-chat setting,
    # expose it to the provider clients for this turn, clear afterwards.
    set_thinking_enabled(
        await read_thinking_enabled(db_client, request.conversation_id, default=True)
    )
    try:
        response = await _run_with_processing_state(
            db_client=db_client,
            orch=orch,
            conversation_id=request.conversation_id,
            processing_stage=_resolve_request_processing_stage(request),
            runner=lambda: _run_chat_request_with_prepared_message(
                request=request,
                orch=orch,
                db_client=db_client,
                broker=broker,
                llm_call=llm_call,
                stt_client=stt_client,
                run_event_context=run_event_context,
                run_event_recorder=run_event_recorder,
            ),
        )
        await _publish_final_chat_event(
            request_stream_queue=request_stream_queue,
            conversation_id=request.conversation_id,
            response=response,
        )
        return response
    except Exception:
        logger.exception("Chat request failed after stream start")
        await _publish_chat_error_event(
            request_stream_queue=request_stream_queue,
            conversation_id=request.conversation_id,
            message="Internal error while processing the chat request.",
        )
        raise
    finally:
        orch.prompt_builder.suggestions_enabled = True
        set_thinking_enabled(None)
        await _finish_request_event_forwarding(
            request_stream_queue=request_stream_queue,
            event_forwarder=event_forwarder,
            orch=orch,
        )


async def _run_chat_request_with_prepared_message(
    *,
    request: ChatRequest,
    orch: object,
    db_client: DBProxyClient,
    broker: object,
    llm_call: Callable,
    stt_client: object | None,
    run_event_context: RunEventContext | None = None,
    run_event_recorder: RunEventRecorder | None = None,
) -> ChatResponse:
    """Prepare one chat request message and run it through the orchestrator."""
    agent_run = await db_client.create_agent_run(
        conversation_id=request.conversation_id,
        run_id=request.run_id or None,
        session_id=request.session_id,
        channel=request.channel,
        request_id=request.request_id,
    )
    agent_run_id = str((agent_run or {}).get("id", ""))
    if run_event_context is not None:
        run_event_context.run_id = agent_run_id
    if run_event_recorder is not None:
        await run_event_recorder.record(
            AgentRunEventType.RUN_STARTED,
            {"channel": request.channel},
        )
    try:
        try:
            message, transcription = await _prepare_request_message(
                request=request,
                stt_client=stt_client,
                broker=broker,
            )
        except VoiceTranscriptionError:
            logger.error("Voice transcription failed", exc_info=True)
            await db_client.update_agent_run_status(
                run_id=agent_run_id,
                status="failed",
                current_step="voice_transcription",
            )
            if run_event_recorder is not None:
                await run_event_recorder.record(
                    AgentRunEventType.RUN_FAILED,
                    {"current_step": "voice_transcription"},
                )
            return _build_voice_transcription_failure_response(
                request.request_id,
            )
        response = await _run_prepared_chat_request(
            request=request,
            orch=orch,
            llm_call=llm_call,
            message=message,
            transcription=transcription,
            agent_run_id=agent_run_id,
        )
    except asyncio.CancelledError:
        if agent_run_id:
            await db_client.update_agent_run_status(
                run_id=agent_run_id,
                status="stopped",
                current_step="stopped",
            )
        if run_event_recorder is not None:
            await run_event_recorder.record(
                AgentRunEventType.RUN_STOPPED,
                {"current_step": "stopped"},
            )
        raise
    except Exception:
        if agent_run_id:
            await db_client.update_agent_run_status(
                run_id=agent_run_id,
                status="failed",
                current_step="exception",
            )
        if run_event_recorder is not None:
            await run_event_recorder.record(
                AgentRunEventType.RUN_FAILED,
                {"current_step": "exception"},
            )
        raise
    if agent_run_id:
        pending = getattr(orch, "pending_red_calls", [])
        status = "waiting" if pending else "completed"
        current_step = "pending_action" if pending else "completed"
        await db_client.update_agent_run_status(
            run_id=agent_run_id,
            status=status,
            current_step=current_step,
        )
        if run_event_recorder is not None and not pending:
            await run_event_recorder.record(
                AgentRunEventType.RUN_COMPLETED,
                {"current_step": current_step},
            )
    return response


def _start_request_event_forwarding(
    *,
    request_id: str,
    ipc_server: OrchestratorIPCServer,
    orch: object,
    run_event_recorder: RunEventRecorder | None = None,
) -> tuple[asyncio.Queue, asyncio.Task]:
    """Create the per-request queue, start forwarding, and bind it to the runtime."""
    request_stream_queue: asyncio.Queue = asyncio.Queue()
    event_forwarder = asyncio.create_task(
        _forward_request_events_to_ipc(
            request_id=request_id,
            request_stream_queue=request_stream_queue,
            ipc_server=ipc_server,
            run_event_recorder=run_event_recorder,
        )
    )
    orch.sse_queue = request_stream_queue
    return request_stream_queue, event_forwarder


async def _run_orchestrator_message(
    *,
    orch: object,
    message: str,
    channel: str,
    llm_call: Callable,
    session_id: str,
    conversation_id: str,
    images: list[str] | None = None,
    forced_tools: list[str] | None = None,
    attached_files: list[str] | None = None,
    meta: dict | None = None,
    agent_run_id: str = "",
) -> AgentResponse:
    """Run one message through the shared orchestrator chat path."""
    return await orch.process_message(
        message=message,
        channel=channel,
        llm_call=llm_call,
        session_id=session_id,
        conversation_id=conversation_id,
        images=images,
        forced_tools=forced_tools,
        attached_files=attached_files,
        meta=meta,
        agent_run_id=agent_run_id,
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
            request_stream_queue, event_forwarder = _start_request_event_forwarding(
                request_id=request.request_id,
                ipc_server=ipc_server_ref(),
                orch=orch,
            )
            try:
                response = _build_missing_llm_response(request.request_id)
                await _publish_final_chat_event(
                    request_stream_queue=request_stream_queue,
                    conversation_id=request.conversation_id,
                    response=response,
                )
                return response
            finally:
                await _finish_request_event_forwarding(
                    request_stream_queue=request_stream_queue,
                    event_forwarder=event_forwarder,
                    orch=orch,
                )
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
