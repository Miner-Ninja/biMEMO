"""Tests for the chat request run lifecycle.

Covers the orchestrator request handler's event streaming (missing
LLM final response, failure, success, cancellation) and the
duplicate-run replay/terminalization behavior for dead turns.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.agent.agent_loop_models import AgentResponse
from backend.ipc.models import (
    ChatRequest,
    StreamEvent,
    StreamEventType,
)
from backend.llm.structured_response import normalize_legacy_response_text
from backend.processes.orchestrator.chat_handler import (
    _build_orchestrator_request_handler,
    _run_chat_request,
    _run_chat_request_with_prepared_message,
)


def _core_chat_db(*, preseed_run: str | None = None) -> SimpleNamespace:
    """Build a stateful core-authority DB stand-in for chat lifecycle tests."""
    template = {
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
    turns: dict[str, dict] = {}

    def _materialize(turn_id: str) -> dict:
        if turn_id not in turns:
            turn = dict(template)
            turn["turn_id"] = turn_id
            turns[turn_id] = turn
        return turns[turn_id]

    if preseed_run:
        _materialize(preseed_run)

    async def get_agent_turn(turn_id: str):
        return turns.get(turn_id)

    async def create_agent_turn(**kwargs):
        turn_id = kwargs.get("turn_id") or f"generated-{len(turns) + 1}"
        return {"turn": _materialize(turn_id)}

    async def commit_agent_turn_finalization(**kwargs):
        turn = _materialize(kwargs["turn_id"])
        turn["terminal_outcome"] = kwargs["outcome_kind"]
        turn["outcome_payload"] = kwargs["outcome_payload"]
        return {"turn": turn}

    async def cancel_agent_turn(turn_id: str, **_kwargs):
        turn = _materialize(turn_id)
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


def _dead_turn_db() -> SimpleNamespace:
    """A core-turn stand-in whose orphaned run restarts as a fresh turn."""
    old_turn = {
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
    new_turn = {
        "turn_id": "run-2",
        "conversation_id": "conv-1",
        "session_id": "session-1",
        "channel": "web",
        "tool_policy_version": "registry-v1",
        "finalization_id": "final-2",
        "terminal_outcome": None,
        "outcome_payload": None,
        "confirmations": [],
        "pending_inputs": [],
    }

    async def get_agent_turn(turn_id: str):
        return old_turn if turn_id == "run-1" else new_turn

    async def create_agent_turn(**kwargs):
        assert kwargs["turn_id"] is None
        return {"turn": new_turn}

    async def commit_agent_turn_finalization(**kwargs):
        assert kwargs["turn_id"] == "run-1"
        old_turn["terminal_outcome"] = kwargs["outcome_kind"]
        old_turn["outcome_payload"] = kwargs["outcome_payload"]
        return {"turn": old_turn}

    return SimpleNamespace(
        get_agent_turn=AsyncMock(side_effect=get_agent_turn),
        create_agent_turn=AsyncMock(side_effect=create_agent_turn),
        commit_agent_turn_finalization=AsyncMock(
            side_effect=commit_agent_turn_finalization
        ),
        cancel_agent_turn=AsyncMock(),
        append_agent_turn_event=AsyncMock(return_value={"event_id": "event-1"}),
        list_pending_agent_confirmations=AsyncMock(return_value=[]),
        update_conversation_processing_state=AsyncMock(),
        send_operation=AsyncMock(return_value={}),
    )


class _FakeIPCServer:
    """Minimal IPC server stand-in that records pushed events."""

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    async def push_event(self, event: StreamEvent) -> None:
        """Record one event for later assertions."""
        self.events.append(event)


def test_orchestrator_handler_streams_missing_llm_final_response() -> None:
    """No-LLM fallback still reaches the browser stream in start-only mode."""
    ipc_server = _FakeIPCServer()
    orch = SimpleNamespace(stream_queue=None)
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
        stream_queue=None,
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
        stream_queue=None,
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
        stream_queue=None,
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


def test_in_flight_duplicate_run_replays_without_new_turn() -> None:
    """The same run_id on a live turn answers without starting a new one."""
    ipc_server = _FakeIPCServer()
    orch = SimpleNamespace(
        stream_queue=None,
        current_run_id="run-1",
        process_message=AsyncMock(),
        pending_red_calls=[],
    )
    db_client = _core_chat_db(preseed_run="run-1")

    response = asyncio.run(
        _run_chat_request(
            request=ChatRequest(
                request_id="req-dup",
                run_id="run-1",
                message="hello",
                channel="web",
                session_id="session-1",
                conversation_id="conv-1",
                client_message_id="cm-1",
            ),
            orch=orch,
            ipc_server=ipc_server,
            db_client=db_client,
            broker=SimpleNamespace(),
            llm_call=AsyncMock(),
            stt_client=None,
        )
    )

    assert response.request_id == "req-dup"
    assert response.run_id == "run-1"
    assert response.outcome_kind is None
    orch.process_message.assert_not_awaited()
    db_client.create_agent_turn.assert_not_awaited()
    db_client.commit_agent_turn_finalization.assert_not_awaited()


def test_dead_duplicate_run_terminalizes_and_restarts() -> None:
    """The same run_id on a dead turn closes it and delivers a fresh turn."""
    ipc_server = _FakeIPCServer()
    orch = SimpleNamespace(
        stream_queue=None,
        current_run_id="",
        process_message=AsyncMock(
            return_value=AgentResponse(
                structured_content=normalize_legacy_response_text("Retry done."),
                loop_result=object(),
            )
        ),
        pending_red_calls=[],
    )
    db_client = _dead_turn_db()

    response = asyncio.run(
        _run_chat_request(
            request=ChatRequest(
                request_id="req-dead",
                run_id="run-1",
                message="hello",
                channel="web",
                session_id="session-1",
                conversation_id="conv-1",
                client_message_id="cm-1",
            ),
            orch=orch,
            ipc_server=ipc_server,
            db_client=db_client,
            broker=SimpleNamespace(),
            llm_call=AsyncMock(),
            stt_client=None,
        )
    )

    db_client.commit_agent_turn_finalization.assert_awaited_once()
    assert (
        db_client.commit_agent_turn_finalization.await_args.kwargs["outcome_kind"]
        == "failed"
    )
    db_client.create_agent_turn.assert_awaited_once()
    assert db_client.create_agent_turn.await_args.kwargs["turn_id"] is None
    orch.process_message.assert_awaited_once()
    process_kwargs = orch.process_message.await_args.kwargs
    assert process_kwargs["agent_run_id"] == "run-2"
    assert process_kwargs["client_message_id"] == "cm-1"
    assert response.structured_content.answer == "Retry done."
