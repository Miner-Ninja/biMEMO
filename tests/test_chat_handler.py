"""Tests for structured assistant-response helpers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.agent.agent_loop_models import AgentResponse
from backend.ipc.models import (
    ChatRequest,
    StreamEvent,
    StreamEventType,
)
from backend.ipc.models import (
    ChatResponse as IPCChatResponse,
)
from backend.ipc.models import (
    StructuredAssistantContent as IPCStructuredAssistantContent,
)
from backend.llm.structured_response import normalize_legacy_response_text
from backend.models.responses import StructuredAssistantContent
from backend.processes.fastapi_app.routes.chat_helpers import (
    build_conversation_message_item,
    build_http_chat_response,
    list_conversation_message_items,
)
from backend.processes.orchestrator.chat_handler import (
    _build_chat_response,
    _build_orchestrator_request_handler,
    _chat_response_final_event_data,
    _forward_request_events_to_ipc,
    _mark_conversation_processing_active,
    _mark_conversation_processing_idle,
    _pending_confirmations_from_orchestrator,
    _push_conversation_updated_event,
    _run_chat_request,
    _run_chat_request_with_prepared_message,
)
from backend.trust.confirmation import PendingAction


def _core_chat_db() -> SimpleNamespace:
    """Build a stateful core-authority DB stand-in for chat lifecycle tests."""
    turn = {
        "turn_id": "run-1",
        "conversation_id": "conv-1",
        "session_id": "session-1",
        "channel": "web",
        "tool_policy_version": "registry-v1",
        "finalization_id": "final-1",
        "terminal_outcome": None,
        "outcome_payload": None,
        "confirmations": [],
        "pending_inputs": [],
    }

    async def get_agent_turn(_turn_id: str):
        return turn if _turn_id == "run-1" else None

    async def create_agent_turn(**_kwargs):
        return {"turn": turn}

    async def commit_agent_turn_finalization(**kwargs):
        turn["terminal_outcome"] = kwargs["outcome_kind"]
        turn["outcome_payload"] = kwargs["outcome_payload"]
        return {"turn": turn}

    async def cancel_agent_turn(_turn_id: str, **_kwargs):
        turn["terminal_outcome"] = "cancelled"
        return {"turn": turn}

    return SimpleNamespace(
        get_agent_turn=AsyncMock(side_effect=get_agent_turn),
        create_agent_turn=AsyncMock(side_effect=create_agent_turn),
        commit_agent_turn_finalization=AsyncMock(
            side_effect=commit_agent_turn_finalization
        ),
        cancel_agent_turn=AsyncMock(side_effect=cancel_agent_turn),
        append_agent_turn_event=AsyncMock(return_value={"event_id": "event-1"}),
        list_pending_agent_confirmations=AsyncMock(return_value=[]),
        update_conversation_processing_state=AsyncMock(),
        send_operation=AsyncMock(return_value={}),
    )


def _paused_input_turn(*, conversation_id: str = "conversation-1") -> dict:
    return {
        "turn_id": "turn-1",
        "conversation_id": conversation_id,
        "session_id": "session-1",
        "channel": "web",
        "terminal_outcome": "paused",
        "finalization_id": "final-1",
        "outcome_payload": None,
        "confirmations": [],
        "pending_inputs": [
            {
                "pending_input_id": "input-1",
                "question": "How many?",
                "expected_input_schema": {"type": "integer"},
                "status": "pending",
                "cas_version": 1,
                "expires_at": "2999-01-01T00:00:00+00:00",
            }
        ],
    }


async def test_invalid_pending_input_returns_typed_paused_error(monkeypatch) -> None:
    """Validation failures keep the canonical prompt and identify the bad reply."""
    monkeypatch.setattr(
        "backend.processes.orchestrator.chat_handler.get_settings",
        lambda: SimpleNamespace(llm_response_stream=False),
    )
    turn = _paused_input_turn()
    db_client = SimpleNamespace(
        get_agent_turn=AsyncMock(return_value=turn),
        create_agent_turn=AsyncMock(),
        reply_agent_pending_input=AsyncMock(
            return_value={
                "status": "pending",
                "validation_error": "pending input requires an integer",
            }
        ),
    )
    response = await _run_chat_request_with_prepared_message(
        request=ChatRequest(
            request_id="request-1",
            run_id="turn-1",
            message="not a number",
            channel="web",
            session_id="session-1",
            conversation_id="conversation-1",
            pending_input_id="input-1",
            pending_input_version=1,
            pending_input_value="not a number",
        ),
        orch=SimpleNamespace(),
        db_client=db_client,
        broker=SimpleNamespace(),
        llm_call=AsyncMock(),
        stt_client=None,
    )
    assert response.outcome_kind.value == "paused"
    assert response.pending_input.pending_input_id == "input-1"
    assert response.error.code == "invalid_pending_input"
    db_client.reply_agent_pending_input.assert_awaited_once_with(
        turn_id="turn-1",
        pending_input_id="input-1",
        conversation_id="conversation-1",
        cas_version=1,
        value="not a number",
    )


async def test_pending_input_cannot_cross_conversations(monkeypatch) -> None:
    """A caller cannot resume a known run that belongs to another conversation."""
    monkeypatch.setattr(
        "backend.processes.orchestrator.chat_handler.get_settings",
        lambda: SimpleNamespace(llm_response_stream=False),
    )
    db_client = SimpleNamespace(
        get_agent_turn=AsyncMock(return_value=_paused_input_turn()),
        create_agent_turn=AsyncMock(),
        reply_agent_pending_input=AsyncMock(),
    )
    response = await _run_chat_request_with_prepared_message(
        request=ChatRequest(
            request_id="request-2",
            run_id="turn-1",
            message="7",
            channel="web",
            session_id="session-2",
            conversation_id="conversation-2",
            pending_input_id="input-1",
            pending_input_version=1,
            pending_input_value=7,
        ),
        orch=SimpleNamespace(),
        db_client=db_client,
        broker=SimpleNamespace(),
        llm_call=AsyncMock(),
        stt_client=None,
    )
    assert response.error.code == "pending_input_conversation_mismatch"
    db_client.reply_agent_pending_input.assert_not_awaited()


def test_build_structured_content_extracts_sql_and_suggestions() -> None:
    """Extract SQL and suggestion blocks from one assistant answer."""
    structured = normalize_legacy_response_text(
        "\n".join(
            [
                "Main answer.",
                "",
                "```sql",
                "SELECT 1",
                "```",
                "",
                "```suggestions",
                "Next step",
                "Another step",
                "```",
            ]
        )
    )
    assert structured.answer == "Main answer."
    assert structured.suggestions == ["Next step", "Another step"]


def test_build_structured_content_drops_language_mismatched_suggestions() -> None:
    """Drop suggestions when their script does not match the answer."""
    structured = normalize_legacy_response_text(
        "\n".join(
            [
                "Русский ответ.",
                "",
                "```suggestions",
                "Open the report",
                "Check the dashboard",
                "```",
            ]
        )
    )
    assert structured.answer == "Русский ответ."
    assert structured.suggestions == []


def test_build_conversation_message_item_uses_saved_structured_content() -> None:
    """Prefer saved structured content when assistant metadata already has it."""
    message_item = build_conversation_message_item(
        {
            "id": "message-1",
            "role": "assistant",
            "content": "Raw answer\n```sql\nSELECT 1\n```",
            "conversation_id": "conversation-1",
            "created_at": "2026-04-12T10:00:00",
            "meta": (
                '{"structured_content": {"answer": "Visible answer", '
                '"thinking": "Internal plan", "sql": "SELECT 1", '
                '"suggestions": ["Next step"]}}'
            ),
        }
    )

    assert message_item.content == "Visible answer"
    assert message_item.structured_content == StructuredAssistantContent(
        answer="Visible answer",
        thinking="Internal plan",
        suggestions=["Next step"],
    )


def test_build_conversation_message_item_uses_content_as_saved_answer() -> None:
    """Read the visible answer from content when new metadata omits it."""
    message_item = build_conversation_message_item(
        {
            "id": "message-1b",
            "role": "assistant",
            "content": "Visible answer",
            "conversation_id": "conversation-1",
            "created_at": "2026-04-12T10:00:00",
            "meta": (
                '{"structured_content": {"thinking": "Internal plan", '
                '"sql": "SELECT 1", "suggestions": ["Next step"]}}'
            ),
        }
    )

    assert message_item.content == "Visible answer"
    assert message_item.structured_content == StructuredAssistantContent(
        answer="Visible answer",
        thinking="Internal plan",
        suggestions=["Next step"],
    )


def test_build_conversation_message_item_normalizes_legacy_assistant_text() -> None:
    """Normalize old assistant messages when structured content is missing."""
    message_item = build_conversation_message_item(
        {
            "id": "message-2",
            "role": "assistant",
            "content": "\n".join(
                [
                    "Main answer.",
                    "",
                    "```sql",
                    "SELECT 1",
                    "```",
                    "",
                    "```suggestions",
                    "Next step",
                    "```",
                ]
            ),
            "conversation_id": "conversation-1",
            "created_at": "2026-04-12T10:00:00",
            "meta": None,
        }
    )

    assert message_item.content == "Main answer."
    assert message_item.structured_content == StructuredAssistantContent(
        answer="Main answer.",
        suggestions=["Next step"],
    )


def test_build_chat_response_uses_structured_answer_as_visible_text() -> None:
    """Return the normalized answer text when structured content is available."""
    chat_response = _build_chat_response(
        request_id="request-1",
        response=AgentResponse(
            structured_content=normalize_legacy_response_text(
                "\n".join(
                    [
                        "Main answer.",
                        "",
                        "```sql",
                        "SELECT 1",
                        "```",
                    ]
                )
            ),
            loop_result=object(),
        ),
        transcription=None,
    )

    assert chat_response.structured_content is not None
    assert chat_response.structured_content.answer == "Main answer."
    assert chat_response.structured_content.suggestions == []


def test_pending_confirmations_from_orchestrator_serializes_red_calls() -> None:
    """Expose RED-zone pending actions in the IPC chat response payload."""
    orch = SimpleNamespace(
        pending_red_calls=[
            PendingAction(
                action_id="action-1",
                tool_name="http_fetch",
                parameters={"url": "https://example.com"},
                channel="web",
                created_at=0.0,
            )
        ]
    )

    assert _pending_confirmations_from_orchestrator(orch) == [
        {
            "action_id": "action-1",
            "tool_name": "http_fetch",
            "parameters": {"url": "https://example.com"},
            "grant_capable": True,
            "url_host": "example.com",
        }
    ]


@pytest.mark.parametrize("stream_enabled_value", ["true", "false"])
def test_build_chat_response_propagates_stream_enabled_setting(
    monkeypatch: pytest.MonkeyPatch,
    stream_enabled_value: str,
) -> None:
    """Mirror BIMEMO_LLM_RESPONSE_STREAM onto the IPC ChatResponse."""
    from backend.config import reset_settings

    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_STREAM", stream_enabled_value)
    reset_settings()
    try:
        chat_response = _build_chat_response(
            request_id="request-stream",
            response=AgentResponse(
                structured_content=normalize_legacy_response_text("Hi."),
                loop_result=object(),
            ),
            transcription=None,
        )
    finally:
        reset_settings()

    assert chat_response.stream_enabled is (stream_enabled_value == "true")


def test_build_http_chat_response_creates_structured_answer_from_text() -> None:
    """Expose one structured answer block even for plain IPC chat responses."""
    http_response = build_http_chat_response(
        conversation_id="conversation-1",
        ipc_response=IPCChatResponse(
            request_id="request-1",
            structured_content=IPCStructuredAssistantContent(answer="Plain answer"),
            input_filtered=False,
            output_filtered=False,
            stream_enabled=True,
        ),
    )

    assert http_response.structured_content == StructuredAssistantContent(
        answer="Plain answer",
        thinking=None,
        suggestions=[],
    )
    assert http_response.stream_enabled is True


def test_chat_response_final_event_data_mirrors_chat_payload() -> None:
    """Build the stream-final event from the same IPC chat response fields."""
    chat_response = IPCChatResponse(
        request_id="request-final",
        structured_content=IPCStructuredAssistantContent(
            answer="Visible answer",
            thinking="Internal plan",
            suggestions=["Next step"],
        ),
        input_filtered=False,
        output_filtered=True,
        pending_confirmations=[
            {
                "action_id": "action-1",
                "tool_name": "http_fetch",
                "parameters": {"url": "https://example.com"},
            }
        ],
        retrieved_memories=[{"summary": "Memory", "date": "2026-05-10"}],
        retrieved_messages=[{"role": "user", "content": "Earlier"}],
        transcription="voice text",
        stats=None,
        stream_enabled=True,
    )

    event_data = _chat_response_final_event_data(
        conversation_id="conversation-1",
        response=chat_response,
    )

    assert event_data == {
        "type": "final",
        "conversation_id": "conversation-1",
        "input_filtered": False,
        "output_filtered": True,
        "pending_confirmations": [
            {
                "action_id": "action-1",
                "tool_name": "http_fetch",
                "parameters": {"url": "https://example.com"},
            }
        ],
        "pause_reason": None,
        "pending_input": None,
        "retrieved_memories": [{"summary": "Memory", "date": "2026-05-10"}],
        "retrieved_messages": [{"role": "user", "content": "Earlier"}],
        "transcription": "voice text",
        "stats": None,
        "structured_content": {
            "answer": "Visible answer",
            "thinking": "Internal plan",
            "suggestions": ["Next step"],
        },
        "stream_enabled": True,
    }


class _FakeIPCServer:
    """Minimal IPC server stand-in that records pushed events."""

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    async def push_event(self, event: StreamEvent) -> None:
        """Record one event for later assertions."""
        self.events.append(event)


def test_push_conversation_updated_event_with_bound_queue() -> None:
    """The helper pushes a formatted dict onto the bound stream queue."""
    queue: asyncio.Queue = asyncio.Queue()
    orch = SimpleNamespace(sse_queue=queue)

    asyncio.run(
        _push_conversation_updated_event(
            orch=orch,
            conversation_id="conv-1",
            processing_status="active",
            processing_stage="Processing...",
            processing_started_at="2026-04-24T20:00:00.000",
        )
    )

    assert queue.qsize() == 1
    event = queue.get_nowait()
    assert event == {
        "type": "conversation_updated",
        "data": {
            "conversation_id": "conv-1",
            "processing_status": "active",
            "processing_stage": "Processing...",
            "processing_started_at": "2026-04-24T20:00:00.000",
        },
    }


def test_push_conversation_updated_event_no_queue_is_noop() -> None:
    """Helper returns silently when orch has no bound stream queue."""
    orch = SimpleNamespace(sse_queue=None)

    asyncio.run(
        _push_conversation_updated_event(
            orch=orch,
            conversation_id="conv-1",
            processing_status="idle",
            processing_stage="",
            processing_started_at=None,
        )
    )


def test_mark_conversation_processing_idle_emits_sse_event() -> None:
    """Marking idle writes to DB and pushes a conversation_updated event."""
    queue: asyncio.Queue = asyncio.Queue()
    orch = SimpleNamespace(sse_queue=queue)
    db_client = SimpleNamespace(
        update_conversation_processing_state=AsyncMock(return_value=None)
    )

    asyncio.run(
        _mark_conversation_processing_idle(
            db_client=db_client,
            orch=orch,
            conversation_id="conv-42",
        )
    )

    db_client.update_conversation_processing_state.assert_awaited_once_with(
        "conv-42",
        "idle",
        processing_stage="",
        processing_started_at=None,
    )
    assert queue.qsize() == 1
    event = queue.get_nowait()
    assert event["type"] == "conversation_updated"
    assert event["data"] == {
        "conversation_id": "conv-42",
        "processing_status": "idle",
        "processing_stage": "",
        "processing_started_at": None,
    }


def test_mark_conversation_processing_active_emits_sse_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marking active writes to DB and pushes a conversation_updated event."""
    monkeypatch.setattr(
        "backend.processes.orchestrator.process_helpers.current_processing_timestamp",
        lambda: "2026-04-25T10:00:00.000",
    )
    queue: asyncio.Queue = asyncio.Queue()
    orch = SimpleNamespace(sse_queue=queue)
    db_client = SimpleNamespace(
        update_conversation_processing_state=AsyncMock(return_value=None)
    )

    asyncio.run(
        _mark_conversation_processing_active(
            db_client=db_client,
            orch=orch,
            conversation_id="conv-7",
            processing_stage="Processing...",
        )
    )

    db_client.update_conversation_processing_state.assert_awaited_once_with(
        "conv-7",
        "active",
        processing_stage="Processing...",
        processing_started_at="2026-04-25T10:00:00.000",
    )
    assert queue.qsize() == 1
    event = queue.get_nowait()
    assert event["data"] == {
        "conversation_id": "conv-7",
        "processing_status": "active",
        "processing_stage": "Processing...",
        "processing_started_at": "2026-04-25T10:00:00.000",
    }


def test_forwarder_maps_conversation_updated_to_stream_event_type() -> None:
    """The forwarder turns a conversation_updated dict into a stream event."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "conversation_updated",
            "data": {
                "conversation_id": "conv-9",
                "processing_status": "idle",
                "processing_stage": "",
                "processing_started_at": None,
            },
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-1",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.CONVERSATION_UPDATED,
        StreamEventType.DONE,
    ]
    conversation_event = ipc_server.events[0]
    assert conversation_event.request_id == "req-1"
    assert conversation_event.data == {
        "conversation_id": "conv-9",
        "processing_status": "idle",
        "processing_stage": "",
        "processing_started_at": None,
    }


def test_forwarder_maps_confirmation_to_stream_event_type() -> None:
    """The forwarder passes confirmation lifecycle events through."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "confirmation",
            "status": "waiting_confirmation",
            "action_id": "act-1",
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-3",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.CONFIRMATION,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[0].data["status"] == "waiting_confirmation"
    assert ipc_server.events[0].data["action_id"] == "act-1"


def test_forwarder_maps_final_to_stream_event_type() -> None:
    """The forwarder passes final assistant payload events through."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "final",
            "conversation_id": "conv-1",
            "structured_content": {"answer": "Done", "suggestions": []},
            "pending_confirmations": [],
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-final",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.FINAL,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[0].data["type"] == "final"
    assert ipc_server.events[0].data["structured_content"]["answer"] == "Done"


def test_forwarder_maps_error_to_stream_event_type() -> None:
    """The forwarder passes terminal stream errors through."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "error",
            "conversation_id": "conv-1",
            "message": "Request failed",
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-error",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.ERROR,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[0].data["type"] == "error"
    assert ipc_server.events[0].data["message"] == "Request failed"


def test_orchestrator_handler_streams_missing_llm_final_response() -> None:
    """No-LLM fallback still reaches the browser stream in start-only mode."""
    ipc_server = _FakeIPCServer()
    orch = SimpleNamespace(sse_queue=None)
    handler = _build_orchestrator_request_handler(
        orch=orch,
        ipc_server_ref=lambda: ipc_server,
        db_client=SimpleNamespace(),
        broker=SimpleNamespace(),
        get_llm_call=lambda: None,
        stt_client=None,
    )

    response = asyncio.run(
        handler(
            ChatRequest(
                request_id="req-no-llm",
                run_id="run-no-llm",
                message="hello",
                channel="web",
                session_id="session-1",
                conversation_id="conv-1",
            )
        )
    )

    assert "No LLM provider configured" in response.structured_content.answer
    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.FINAL,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[0].data["type"] == "final"
    assert (
        "No LLM provider configured"
        in ipc_server.events[0].data["structured_content"]["answer"]
    )


def test_chat_request_failure_streams_error_and_done() -> None:
    """A failed chat turn must terminate the browser stream."""
    ipc_server = _FakeIPCServer()
    orch = SimpleNamespace(
        sse_queue=None,
        process_message=AsyncMock(side_effect=TimeoutError("llm timeout")),
        pending_red_calls=[],
    )
    db_client = _core_chat_db()

    response = asyncio.run(
        _run_chat_request(
            request=ChatRequest(
                request_id="req-timeout",
                run_id="run-timeout",
                message="hello",
                channel="web",
                session_id="session-1",
                conversation_id="conv-1",
            ),
            orch=orch,
            ipc_server=ipc_server,
            db_client=db_client,
            broker=SimpleNamespace(),
            llm_call=AsyncMock(),
            stt_client=None,
        )
    )

    assert response.outcome_kind.value == "failed"
    assert [event.event_type for event in ipc_server.events][-2:] == [
        StreamEventType.FAILED,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[-2].data["type"] == "failed"
    recorded_event_types = [
        call.kwargs["event_type"]
        for call in db_client.append_agent_turn_event.await_args_list
    ]
    assert "run_started" in recorded_event_types
    assert "run_failed" in recorded_event_types
    assert "run_completed" not in recorded_event_types
    assert (
        db_client.update_conversation_processing_state.await_args_list[-1].args[1]
        == "idle"
    )


def test_chat_request_success_records_completed_runtime_event() -> None:
    """A successful chat turn records started and completed runtime events."""
    ipc_server = _FakeIPCServer()
    orch = SimpleNamespace(
        sse_queue=None,
        process_message=AsyncMock(
            return_value=AgentResponse(
                structured_content=normalize_legacy_response_text("Done."),
                loop_result=object(),
            )
        ),
        pending_red_calls=[],
    )
    db_client = _core_chat_db()

    response = asyncio.run(
        _run_chat_request(
            request=ChatRequest(
                request_id="req-success",
                run_id="run-success",
                message="hello",
                channel="web",
                session_id="session-1",
                conversation_id="conv-1",
            ),
            orch=orch,
            ipc_server=ipc_server,
            db_client=db_client,
            broker=SimpleNamespace(),
            llm_call=AsyncMock(),
            stt_client=None,
        )
    )

    recorded_event_types = [
        call.kwargs["event_type"]
        for call in db_client.append_agent_turn_event.await_args_list
    ]
    assert response.structured_content.answer == "Done."
    assert "run_started" in recorded_event_types
    assert "final_accepted" in recorded_event_types
    assert (
        db_client.update_conversation_processing_state.await_args_list[-1].args[1]
        == "idle"
    )


def test_chat_request_cancellation_records_stopped_runtime_event() -> None:
    """Cancelling a chat turn marks the runtime run as stopped."""
    ipc_server = _FakeIPCServer()
    started = asyncio.Event()

    async def slow_process_message(**kwargs):
        """Block until the test cancels the chat request."""
        del kwargs
        started.set()
        await asyncio.sleep(60)

    orch = SimpleNamespace(
        sse_queue=None,
        process_message=slow_process_message,
        pending_red_calls=[],
    )
    db_client = _core_chat_db()

    async def run_and_cancel() -> None:
        """Start the chat task and cancel it after processing begins."""
        task = asyncio.create_task(
            _run_chat_request(
                request=ChatRequest(
                    request_id="req-cancel",
                    run_id="run-1",
                    message="hello",
                    channel="web",
                    session_id="session-1",
                    conversation_id="conv-1",
                ),
                orch=orch,
                ipc_server=ipc_server,
                db_client=db_client,
                broker=SimpleNamespace(),
                llm_call=AsyncMock(),
                stt_client=None,
            )
        )
        await started.wait()
        task.cancel()
        response = await task
        assert response.outcome_kind.value == "cancelled"

    asyncio.run(run_and_cancel())

    recorded_event_types = [
        call.kwargs["event_type"]
        for call in db_client.append_agent_turn_event.await_args_list
    ]
    db_client.cancel_agent_turn.assert_awaited_once()
    assert "run_stopped" in recorded_event_types
    assert (
        db_client.update_conversation_processing_state.await_args_list[-1].args[1]
        == "idle"
    )


def test_forwarder_skips_unknown_event_types() -> None:
    """Unknown event types do not abort the forwarder loop."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait({"type": "totally_bogus", "data": {}})
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-2",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.DONE,
    ]


class _FakePendingAdminClient:
    """Admin client double serving the core-turn confirmation sources."""

    def __init__(
        self,
        messages: list[dict],
        runs_by_conversation: dict[str, list[dict]] | None = None,
        confirmations_by_run: dict[str, list[dict]] | None = None,
        events_by_run: dict[str, list[dict]] | None = None,
    ):
        self._messages = messages
        self._runs = runs_by_conversation or {}
        self._confirmations = confirmations_by_run or {}
        self._events = events_by_run or {}

    async def list_messages(
        self, limit: int = 50, offset: int = 0, conversation_id: str | None = None
    ) -> list[dict]:
        return self._messages

    async def list_agent_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict]:
        return list(self._runs.get(conversation_id or "", []))[:limit]

    async def list_agent_confirmations(
        self, turn_id: str, limit: int = 100
    ) -> list[dict]:
        return list(self._confirmations.get(turn_id, []))[:limit]

    async def list_agent_run_events(
        self, run_id: str, limit: int = 500
    ) -> list[dict]:
        return list(self._events.get(run_id, []))[:limit]


def test_waiting_confirmation_attaches_to_last_assistant_message() -> None:
    """A persisted un-decided RED card comes back on its assistant message."""
    messages = [
        {
            "id": "user-1",
            "role": "user",
            "content": "run git status",
            "conversation_id": "conv-1",
            "created_at": "2026-08-12T10:00:00",
            "meta": None,
        },
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": "Working on it.",
            "conversation_id": "conv-1",
            "created_at": "2026-08-12T10:00:01",
            "meta": None,
        },
    ]
    admin_client = _FakePendingAdminClient(
        messages=messages,
        runs_by_conversation={"conv-1": [{"id": "run-1"}]},
        confirmations_by_run={
            "run-1": [{"confirmation_id": "action-1", "status": "pending"}]
        },
        events_by_run={
            "run-1": [
                {
                    "event_type": "run_paused",
                    "payload": {
                        "pending_confirmations": [
                            {
                                "action_id": "action-1",
                                "tool_name": "http_fetch",
                                "parameters": {"url": "https://example.com"},
                                "grant_capable": True,
                                "url_host": "example.com",
                            }
                        ]
                    },
                }
            ]
        },
    )

    items = asyncio.run(
        list_conversation_message_items(
            admin_client=admin_client,
            conversation_id="conv-1",
            limit=50,
            offset=0,
        )
    )

    assistant = next(item for item in items if item.id == "assistant-1")
    assert assistant.pending_confirmations
    card = assistant.pending_confirmations[0]
    assert card.action_id == "action-1"
    assert card.tool_name == "http_fetch"
    assert card.grant_capable is True
    assert card.url_host == "example.com"
    user = next(item for item in items if item.id == "user-1")
    assert not user.pending_confirmations


def test_waiting_confirmation_from_other_conversation_is_not_attached() -> None:
    """A decided confirmation never resurfaces on history reload."""
    messages = [
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": "Done.",
            "conversation_id": "conv-1",
            "created_at": "2026-08-12T10:00:01",
            "meta": None,
        },
    ]
    admin_client = _FakePendingAdminClient(
        messages=messages,
        runs_by_conversation={"conv-1": [{"id": "run-1"}]},
        confirmations_by_run={
            "run-1": [{"confirmation_id": "action-1", "status": "approved"}]
        },
        events_by_run={
            "run-1": [
                {
                    "event_type": "run_paused",
                    "payload": {
                        "pending_confirmations": [
                            {
                                "action_id": "action-1",
                                "tool_name": "http_fetch",
                                "parameters": {},
                            }
                        ]
                    },
                }
            ]
        },
    )

    items = asyncio.run(
        list_conversation_message_items(
            admin_client=admin_client,
            conversation_id="conv-1",
            limit=50,
            offset=0,
        )
    )

    assistant = next(item for item in items if item.id == "assistant-1")
    assert not assistant.pending_confirmations


