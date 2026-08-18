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
    PauseReason,
    PendingInput,
    StreamEvent,
    StreamEventType,
    StructuredAssistantContent,
    TurnError,
    TurnOutcomeKind,
)
from backend.llm.call_factory import set_thinking_enabled
from backend.processes.orchestrator.run_event_recorder import (
    AgentRunEventType,
    RunEventContext,
    RunEventRecorder,
)
from backend.security.policy import get_policy
from backend.tools.handlers.registration import confirmation_button_extras


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
    "paused": StreamEventType.PAUSED,
    "failed": StreamEventType.FAILED,
    "cancelled": StreamEventType.CANCELLED,
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
    if response.outcome is not None and response.outcome.confirmations:
        pending_confirmations = [
            {
                "action_id": item.confirmation_id,
                "tool_name": item.tool_name,
                "parameters": item.parameters,
                **confirmation_button_extras(item.tool_name, item.parameters),
            }
            for item in response.outcome.confirmations
        ]
    pending_input = None
    pause_reason = None
    if response.outcome is not None and response.outcome.pending_input is not None:
        item = response.outcome.pending_input
        pause_reason = PauseReason.MISSING_INPUT
        pending_input = PendingInput(
            pending_input_id=item.pending_input_id,
            question=item.question,
            suggestions=list(item.suggestions),
            expected_input_schema=item.expected_input_schema,
            expires_at=item.expires_at,
            cas_version=item.cas_version,
        )
    elif pending_confirmations:
        pause_reason = PauseReason.CONFIRMATION
    return ChatResponse(
        request_id=request_id,
        structured_content=_copy_structured_content(response.structured_content),
        input_filtered=response.input_filtered,
        output_filtered=response.output_filtered,
        retrieved_memories=response.retrieved_memories or None,
        retrieved_messages=response.retrieved_messages or None,
        pending_confirmations=pending_confirmations,
        pause_reason=pause_reason,
        pending_input=pending_input,
        used_tools=used_tools,
        transcription=transcription,
        stats=response.stats,
        stream_enabled=(
            False
            if response.outcome is not None
            else get_settings().llm_response_stream
        ),
        outcome_kind=(
            TurnOutcomeKind(response.outcome.kind.value)
            if response.outcome is not None
            else None
        ),
        run_id=response.run_id or "",
        finalization_id=response.finalization_id,
        error=(
            TurnError(
                code=response.outcome.error.code,
                message=response.outcome.error.message,
                retryable=response.outcome.error.retryable,
            )
            if response.outcome is not None and response.outcome.error is not None
            else None
        ),
    )


def _copy_structured_content(
    structured_content: StructuredAssistantContent | None,
) -> StructuredAssistantContent | None:
    """Copy one structured assistant reply into the IPC model."""
    if structured_content is None:
        return None
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


def _build_replayed_core_response(
    *,
    request_id: str,
    turn: dict[str, object],
) -> ChatResponse | None:
    """Rebuild one typed response from canonical core turn state."""
    raw_kind = str(turn.get("terminal_outcome") or "")
    if raw_kind not in {"final", "paused", "failed", "cancelled"}:
        return None
    kind = TurnOutcomeKind(raw_kind)
    raw_payload = turn.get("outcome_payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw_structured = payload.get("structured_content")
    structured = (
        StructuredAssistantContent.model_validate(raw_structured)
        if isinstance(raw_structured, dict)
        else None
    )
    confirmations: list[dict[str, object]] | None = None
    pause_reason = None
    pending_input = None
    if kind is TurnOutcomeKind.PAUSED:
        raw_confirmations = turn.get("confirmations")
        if isinstance(raw_confirmations, list):
            confirmations = [
                {
                    "action_id": str(item.get("confirmation_id", "")),
                    "tool_name": str(item.get("tool_name", "")),
                    "parameters": item.get("parameters", {}),
                    **confirmation_button_extras(
                        str(item.get("tool_name", "")),
                        item.get("parameters") or {},
                    ),
                }
                for item in raw_confirmations
                if isinstance(item, dict) and item.get("status") == "pending"
            ]
        if confirmations:
            pause_reason = PauseReason.CONFIRMATION
        raw_inputs = turn.get("pending_inputs")
        if isinstance(raw_inputs, list):
            active_inputs = [
                item
                for item in raw_inputs
                if isinstance(item, dict) and item.get("status") == "pending"
            ]
            if active_inputs:
                item = active_inputs[-1]
                pause_reason = PauseReason.MISSING_INPUT
                pending_input = PendingInput(
                    pending_input_id=str(item.get("pending_input_id", "")),
                    question=str(item.get("question", "")),
                    suggestions=[
                        str(value)
                        for value in item.get("suggestions", [])
                        if isinstance(value, str)
                    ],
                    expected_input_schema=dict(item.get("expected_input_schema", {})),
                    expires_at=str(item.get("expires_at", "")),
                    cas_version=int(item.get("cas_version", 1)),
                )
    raw_error = turn.get("terminal_error") or payload.get("error")
    error = TurnError.model_validate(raw_error) if isinstance(raw_error, dict) else None
    return ChatResponse(
        request_id=request_id,
        structured_content=structured,
        pending_confirmations=confirmations,
        pause_reason=pause_reason,
        pending_input=pending_input,
        input_filtered=bool(payload.get("input_filtered", False)),
        output_filtered=bool(payload.get("output_filtered", False)),
        stats=payload.get("stats"),
        stream_enabled=False,
        outcome_kind=kind,
        run_id=str(turn.get("turn_id", "")),
        finalization_id=(
            str(turn.get("finalization_id", ""))
            if kind in {TurnOutcomeKind.FINAL, TurnOutcomeKind.FAILED}
            else None
        ),
        error=error,
    )


def _chat_response_final_event_data(
    *,
    conversation_id: str,
    response: ChatResponse,
) -> dict[str, object]:
    """Build one authoritative typed outcome stream payload."""
    data: dict[str, object] = {
        "type": (
            response.outcome_kind.value
            if response.outcome_kind is not None
            else "final"
        ),
        "conversation_id": conversation_id,
        "input_filtered": response.input_filtered,
        "output_filtered": response.output_filtered,
        "pending_confirmations": response.pending_confirmations or [],
        "pause_reason": (
            response.pause_reason.value if response.pause_reason is not None else None
        ),
        "pending_input": (
            response.pending_input.model_dump(mode="json")
            if response.pending_input is not None
            else None
        ),
        "retrieved_memories": response.retrieved_memories,
        "retrieved_messages": response.retrieved_messages,
        "transcription": response.transcription,
        "stats": response.stats.model_dump(mode="json")
        if response.stats is not None
        else None,
        "structured_content": (
            response.structured_content.model_dump(mode="json")
            if response.structured_content is not None
            else None
        ),
        "stream_enabled": response.stream_enabled,
    }
    if response.outcome_kind is not None:
        data.update(
            {
                "outcome_kind": response.outcome_kind.value,
                "run_id": response.run_id,
                "finalization_id": response.finalization_id,
                "error": (
                    response.error.model_dump(mode="json")
                    if response.error is not None
                    else None
                ),
            }
        )
    return data


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
    tool_policy_version: str = "registry-v1",
    pending_input_resume: dict[str, object] | None = None,
) -> ChatResponse:
    """Run one already-prepared chat message through the orchestrator."""
    request_meta: dict[str, object] = {}
    if transcription:
        request_meta["source"] = "voice"
    if pending_input_resume is not None:
        request_meta["pending_input_resume"] = pending_input_resume
    request_meta["tool_policy_version"] = tool_policy_version
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
        meta=request_meta or None,
        agent_run_id=agent_run_id,
        client_message_id=request.client_message_id or None,
    )
    return _build_chat_response(
        request_id=request.request_id,
        response=response,
        transcription=transcription,
        pending_confirmations=_pending_confirmations_from_orchestrator(orch),
        used_tools=_used_tools_from_response(response),
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
            **confirmation_button_extras(action.tool_name, action.parameters),
        }
        for action in pending
    ]


def _used_tools_from_response(
    response: AgentResponse,
) -> list[dict[str, object]] | None:
    """Return executed tool payloads for the HTTP response."""
    if not get_policy().get_bool("tools.manual_control_enabled"):
        return None
    if not response.used_tools:
        return None
    return list(response.used_tools)


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
    orch.run_event_recorder = run_event_recorder
    prompt_builder = getattr(orch, "prompt_builder", None)
    request_stream_queue, event_forwarder = _start_request_event_forwarding(
        request_id=request.request_id,
        ipc_server=ipc_server,
        orch=orch,
        run_event_recorder=run_event_recorder,
    )
    # Suggestions are on by default in interactive chats; the per-chat toggle
    # can turn them off. Set before the turn (the prompt is built inside) and
    # restored afterwards since the orchestrator is reused across turns.
    if prompt_builder is not None:
        prompt_builder.suggestions_enabled = await read_suggestions_enabled(
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
    except asyncio.CancelledError:
        if run_event_context.run_id:
            cancelled = await db_client.cancel_agent_turn(
                run_event_context.run_id,
                reason="Request cancelled.",
            )
            raw_turn = cancelled.get("turn")
            turn = raw_turn if isinstance(raw_turn, dict) else {}
            response = _build_replayed_core_response(
                request_id=request.request_id,
                turn=turn,
            ) or ChatResponse(
                request_id=request.request_id,
                outcome_kind=TurnOutcomeKind.CANCELLED,
                run_id=run_event_context.run_id,
            )
            await _publish_final_chat_event(
                request_stream_queue=request_stream_queue,
                conversation_id=request.conversation_id,
                response=response,
            )
            return response
        raise
    except Exception:
        logger.exception("Chat request failed after stream start")
        await _publish_chat_error_event(
            request_stream_queue=request_stream_queue,
            conversation_id=request.conversation_id,
            message="Internal error while processing the chat request.",
        )
        raise
    finally:
        orch.run_event_recorder = None
        if prompt_builder is not None:
            prompt_builder.suggestions_enabled = True
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
    get_agent_turn = getattr(db_client, "get_agent_turn", None)
    create_agent_turn = getattr(db_client, "create_agent_turn", None)
    if not callable(get_agent_turn) or not callable(create_agent_turn):
        raise RuntimeError("registry-v1 core authority is unavailable")
    existing_core = await get_agent_turn(request.run_id) if request.run_id else None
    if existing_core is None and not request.pending_input_id:
        permission_reply = await _maybe_handle_permission_reply(
            request=request,
            orch=orch,
            db_client=db_client,
            llm_call=llm_call,
            run_event_context=run_event_context,
        )
        if permission_reply is not None:
            return permission_reply
    pending_input_resume: dict[str, object] | None = None
    if existing_core is None:
        created = await create_agent_turn(
            conversation_id=request.conversation_id,
            turn_id=request.run_id or None,
            session_id=request.session_id,
            channel=request.channel,
        )
        raw_turn = created.get("turn")
        turn = raw_turn if isinstance(raw_turn, dict) else {}
    else:
        turn = existing_core
    agent_run_id = str(turn.get("turn_id", ""))
    if str(turn.get("conversation_id", "")) != request.conversation_id:
        return ChatResponse(
            request_id=request.request_id,
            run_id=agent_run_id,
            error=TurnError(
                code="pending_input_conversation_mismatch",
                message="The pending input does not belong to this conversation.",
                retryable=False,
            ),
        )
    if (
        existing_core is not None
        and not request.pending_input_id
        and not turn.get("terminal_outcome")
    ):
        # A pre-existing non-terminal turn: either this process is still
        # executing it (in-flight duplicate) or its execution is gone
        # (the process restarted mid-turn — a dead/orphaned turn).
        if getattr(orch, "current_run_id", "") == agent_run_id:
            # In-flight replay: the original execution continues; answer
            # the duplicate with the live state of the same run — no new
            # turn, no duplicate message row.
            return ChatResponse(
                request_id=request.request_id,
                run_id=agent_run_id,
            )
        # Dead turn: terminalize it as a failure, then process the
        # message as a fresh turn so the retry is delivered end-to-end.
        await _commit_core_request_failure(
            db_client=db_client,
            request=request,
            turn_id=agent_run_id,
            code="turn_orphaned",
            message=(
                "The previous turn did not complete and was closed as a "
                "failure."
            ),
        )
        created = await create_agent_turn(
            conversation_id=request.conversation_id,
            turn_id=None,
            session_id=request.session_id,
            channel=request.channel,
        )
        raw_turn = created.get("turn")
        turn = raw_turn if isinstance(raw_turn, dict) else {}
        agent_run_id = str(turn.get("turn_id", ""))

    terminal_replay = _build_replayed_core_response(
        request_id=request.request_id,
        turn=turn,
    )
    if terminal_replay is not None and turn.get("terminal_outcome") != "paused":
        return terminal_replay
    if request.pending_input_id:
        pending_input_resume = await db_client.reply_agent_pending_input(
            turn_id=agent_run_id,
            pending_input_id=request.pending_input_id,
            conversation_id=request.conversation_id,
            cas_version=int(request.pending_input_version or 0),
            value=request.pending_input_value,
        )
        validation_error = pending_input_resume.get("validation_error")
        if validation_error:
            canonical_turn = await get_agent_turn(agent_run_id)
            if canonical_turn is None:
                raise RuntimeError("pending-input turn disappeared")
            replay = _build_replayed_core_response(
                request_id=request.request_id,
                turn=canonical_turn,
            )
            if replay is None:
                raise RuntimeError("invalid pending input has no paused response")
            return replay.model_copy(
                update={
                    "error": TurnError(
                        code="invalid_pending_input",
                        message=str(validation_error),
                        retryable=True,
                    )
                }
            )
        if pending_input_resume.get("status") != "answered":
            return await _commit_core_request_failure(
                db_client=db_client,
                request=request,
                turn_id=agent_run_id,
                code="pending_input_expired",
                message="The requested input expired before it was received.",
            )
    elif request.run_id:
        canonical_turn = await get_agent_turn(agent_run_id)
        if canonical_turn is not None:
            replay = _build_replayed_core_response(
                request_id=request.request_id,
                turn=canonical_turn,
            )
            if replay is not None:
                return replay
    pinned_tool_policy_version = str(turn.get("tool_policy_version", "registry-v1"))
    if run_event_context is not None:
        run_event_context.run_id = agent_run_id
        run_event_context.architecture_version = "structured_v1"
    if run_event_recorder is not None:
        await run_event_recorder.record(
            AgentRunEventType.RUN_STARTED,
            {"channel": request.channel},
        )
        if request.forced_tools and get_policy().get_bool(
            "observability.trace_enabled"
        ):
            await run_event_recorder.record(
                AgentRunEventType.FORCED_TOOLS,
                {"forced_tools": list(request.forced_tools)},
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
            failed_response = await _commit_core_request_failure(
                db_client=db_client,
                request=request,
                turn_id=agent_run_id,
                code="voice_transcription_failed",
                message="Voice transcription failed.",
            )
            if run_event_recorder is not None:
                await run_event_recorder.record(
                    AgentRunEventType.RUN_FAILED,
                    {"current_step": "voice_transcription"},
                )
            return failed_response
        response = await _run_prepared_chat_request(
            request=request,
            orch=orch,
            llm_call=llm_call,
            message=message,
            transcription=transcription,
            agent_run_id=agent_run_id,
            pending_input_resume=pending_input_resume,
            tool_policy_version=pinned_tool_policy_version,
        )
    except asyncio.CancelledError:
        if run_event_recorder is not None:
            await run_event_recorder.record(
                AgentRunEventType.RUN_STOPPED,
                {"current_step": "stopped"},
            )
        raise
    except Exception:
        logger.exception(
            "Prepared chat request failed request_id=%s run_id=%s",
            request.request_id,
            agent_run_id,
        )
        if agent_run_id:
            failed_response = await _commit_core_request_failure(
                db_client=db_client,
                request=request,
                turn_id=agent_run_id,
                code="internal_error",
                message="Internal error while processing the chat request.",
            )
            if run_event_recorder is not None:
                await run_event_recorder.record(
                    AgentRunEventType.RUN_FAILED,
                    {"current_step": "exception"},
                )
            return failed_response
        if run_event_recorder is not None:
            await run_event_recorder.record(
                AgentRunEventType.RUN_FAILED,
                {"current_step": "exception"},
            )
        raise
    return response


async def _maybe_handle_permission_reply(
    *,
    request: ChatRequest,
    orch: object,
    db_client: DBProxyClient,
    llm_call: Callable,
    run_event_context: RunEventContext | None,
) -> ChatResponse | None:
    """Return a replayed response while any confirmation is pending.

    The delivery invariant: while ANY confirmation of this conversation is
    pending, EVERY incoming message — typed, voice, or empty — returns the
    paused card and never starts a new turn. Decisions arrive exclusively
    over the /confirm transport.
    """
    list_pending = getattr(db_client, "list_pending_agent_confirmations", None)
    if not callable(list_pending):
        return None
    pending = await list_pending(request.conversation_id)
    if not pending:
        return None
    latest_turn_id = str(pending[-1].get("turn_id", ""))
    canonical_turn = await db_client.get_agent_turn(latest_turn_id)
    if canonical_turn is not None:
        replay = _build_replayed_core_response(
            request_id=request.request_id,
            turn=canonical_turn,
        )
        if replay is not None:
            return replay
    # The turn is mid-execution (a sibling approval reset its terminal
    # outcome) or its row is unavailable — the confirmations are still the
    # authority, so re-present them instead of silently starting a second
    # concurrent turn in the same conversation.
    return ChatResponse(
        request_id=request.request_id,
        run_id=latest_turn_id,
        outcome_kind=TurnOutcomeKind.PAUSED,
        pause_reason=PauseReason.CONFIRMATION,
        pending_confirmations=[
            {
                "action_id": str(item.get("confirmation_id", "")),
                "tool_name": str(item.get("tool_name", "")),
                "parameters": dict(item.get("arguments", {}) or {}),
                **confirmation_button_extras(
                    str(item.get("tool_name", "")),
                    dict(item.get("arguments", {}) or {}),
                ),
            }
            for item in pending
        ],
    )


async def _commit_core_request_failure(
    *,
    db_client: DBProxyClient,
    request: ChatRequest,
    turn_id: str,
    code: str,
    message: str,
) -> ChatResponse:
    """Atomically commit and rebuild one user-safe interactive FAILED outcome."""
    turn = await db_client.get_agent_turn(turn_id)
    if turn is None:
        raise RuntimeError("authoritative turn disappeared during failure commit")
    finalization_id = str(turn.get("finalization_id", ""))
    structured = {"answer": message, "thinking": None, "suggestions": []}
    await db_client.commit_agent_turn_finalization(
        turn_id=turn_id,
        finalization_id=finalization_id,
        outcome_kind="failed",
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        assistant_content=message,
        assistant_meta={"structured_content": structured},
        dialog_content=f"User: {request.message}\n\nAssistant: {message}",
        source_message_ids=[],
        outcome_payload={
            "structured_content": structured,
            "input_filtered": False,
            "output_filtered": False,
            "stats": None,
            "error": {"code": code, "message": message, "retryable": False},
        },
    )
    canonical = await db_client.get_agent_turn(turn_id)
    if canonical is None:
        raise RuntimeError("failed turn could not be replayed")
    replay = _build_replayed_core_response(
        request_id=request.request_id, turn=canonical
    )
    if replay is None:
        raise RuntimeError("failed turn produced no canonical response")
    return replay


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
    client_message_id: str | None = None,
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
        client_message_id=client_message_id,
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
