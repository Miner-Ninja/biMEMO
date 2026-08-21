"""Behaviour contract tests for the orchestrator process helpers (F27).

Covers the pure seams of the orchestrator lifecycle: the confirmation
handler (decision mapping, the typed unknown outcome, error propagation,
and the two stream events it publishes), the run-events replay handler
(run lookup, conversation merge with stable ordering, and the plain
fallback), the memory-event forwarder (forward, unsubscribe on cancel,
surviving a failed push), and the background-task bookkeeping helpers.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from backend.ipc.errors import ConfirmationNotFoundError, OperationNotFoundError
from backend.ipc.ipc_channel import IPCChannelError
from backend.ipc.models import (
    ConfirmRequest,
    ListAgentRunEventsRequest,
    StreamEvent,
    StreamEventType,
)
from backend.processes.orchestrator.client import DBProxyError
from backend.processes.orchestrator.process import (
    _build_confirm_handler,
    _build_list_run_events_handler,
    _cancel_background_tasks,
    _close_llm_client,
    _forward_memory_events_to_ipc,
    _register_background_task,
)


# --- Fakes -----------------------------------------------------------------


class _FakeIPCServer:
    """IPC server stand-in recording every pushed stream event."""

    def __init__(self, fail_first_push_with: Exception | None = None) -> None:
        self.events: list[StreamEvent] = []
        self.fail_first_push_with = fail_first_push_with

    async def push_event(self, event: StreamEvent) -> None:
        """Record the event, optionally failing the first call once."""
        if self.fail_first_push_with is not None:
            error = self.fail_first_push_with
            self.fail_first_push_with = None
            raise error
        self.events.append(event)


class _FakeOrch:
    """Orchestrator stand-in returning a canned core confirmation result."""

    def __init__(
        self,
        core_response: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.core_response = core_response
        self.error = error
        self.calls: list[dict] = []

    async def resume_structured_confirmation(
        self, *, confirmation_id: str, decision: object, scope: str, llm_call: object
    ) -> object:
        """Record the call and return the canned response or raise."""
        self.calls.append(
            {
                "confirmation_id": confirmation_id,
                "decision": decision,
                "scope": scope,
                "llm_call": llm_call,
            }
        )
        if self.error is not None:
            raise self.error
        return self.core_response


def _core_response(*, answer: str = "", assistant_message_id: str = "m-1"):
    """Build the shape of a resumed core turn result."""
    outcome = SimpleNamespace(
        kind=SimpleNamespace(value="final"),
        run_id="run-1",
        finalization_id="fin-1",
        assistant_message_id=assistant_message_id,
        metadata={},
        confirmations=[],
        error=None,
    )
    structured = SimpleNamespace(
        answer=answer,
        thinking=None,
        suggestions=None,
    )
    return SimpleNamespace(outcome=outcome, structured_content=structured)


class _FakeDBClient:
    """DB Proxy client stand-in with canned turn and event tables."""

    def __init__(
        self,
        turn: dict | None = None,
        core_events: list[dict] | None = None,
        runtime_events: list[dict] | None = None,
    ) -> None:
        self.turn = turn
        self.core_events = core_events or []
        self.runtime_events = runtime_events or []
        self.fallback_calls: list[dict] = []

    async def get_agent_turn(self, run_id: str):
        """Return the canned turn for the run."""
        return self.turn

    async def list_agent_turn_events(
        self, *, conversation_id: str, after_sequence: int, limit: int
    ):
        """Return the canned core events."""
        return list(self.core_events)

    async def list_agent_run_events(
        self,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 200,
    ):
        """Return the canned runtime events or record the fallback call."""
        self.fallback_calls.append(
            {
                "run_id": run_id,
                "conversation_id": conversation_id,
                "after_sequence": after_sequence,
                "limit": limit,
            }
        )
        return list(self.runtime_events)


class _FakeMemoryBus:
    """In-process memory event bus stand-in backed by a real queue."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sub_id = "sub-1"
        self.unsubscribed: list[str] = []

    def subscribe(self):
        """Return the subscription id and the event queue."""
        return self.sub_id, self.queue

    def unsubscribe(self, sub_id: str) -> None:
        """Record the unsubscribe."""
        self.unsubscribed.append(sub_id)


# --- Confirmation handler ---------------------------------------------------


class TestConfirmHandler:
    """The IPC confirmation entry point used by FastAPI."""

    def _handler(self, orch: _FakeOrch) -> tuple[object, _FakeIPCServer]:
        """Build a confirm handler around the fakes."""
        server = _FakeIPCServer()
        return (
            _build_confirm_handler(
                orch=orch,  # type: ignore[arg-type]
                db_client=None,  # type: ignore[arg-type]
                get_llm_call=lambda: None,
                ipc_server_ref=lambda: server,  # type: ignore[arg-type]
            ),
            server,
        )

    @pytest.mark.parametrize(
        ("decision", "expected"),
        [
            ("approve", "approved"),
            ("deny", "denied"),
        ],
        ids=["approve", "deny"],
    )
    async def test_decision_maps_to_confirmation_status(
        self, decision: str, expected: str
    ):
        """The user decision is forwarded as the canonical status."""
        from backend.agent.turn_state import ConfirmationStatus

        orch = _FakeOrch(core_response=_core_response())
        handler, _ = self._handler(orch)
        await handler(
            ConfirmRequest(request_id="req-1", action_id="act-1", decision=decision)
        )
        assert orch.calls[0]["decision"] == ConfirmationStatus(expected)

    async def test_success_returns_response_and_pushes_two_events(self):
        """A processed decision reports the outcome status and two events."""
        answer = "Done. The file was saved."
        orch = _FakeOrch(core_response=_core_response(answer=answer))
        handler, server = self._handler(orch)
        response = await handler(
            ConfirmRequest(request_id="req-1", action_id="act-1", decision="approve")
        )
        assert response.accepted is True
        assert response.status == "final"
        assert response.message_id == "m-1"
        assert response.result_summary == answer

        assert [event.event_type for event in server.events] == [
            StreamEventType.CONFIRMATION,
            StreamEventType.FINAL,
        ]
        confirmation = server.events[0].data
        assert confirmation == {
            "action_id": "act-1",
            "status": "final",
            "message_id": "m-1",
        }
        outcome_data = server.events[1].data
        assert outcome_data["outcome_kind"] == "final"
        assert outcome_data["run_id"] == "run-1"
        assert outcome_data["finalization_id"] == "fin-1"
        assert outcome_data["message_id"] == "m-1"
        assert outcome_data["structured_content"]["answer"] == answer

    async def test_result_summary_is_truncated_to_500_chars(self):
        """A long answer is truncated in the response summary."""
        answer = "x" * 600
        orch = _FakeOrch(core_response=_core_response(answer=answer))
        handler, _ = self._handler(orch)
        response = await handler(
            ConfirmRequest(request_id="req-1", action_id="act-1", decision="approve")
        )
        assert response.result_summary == "x" * 500

    async def test_unknown_confirmation_is_typed_outcome(self):
        """An unknown confirmation answers as unknown, not an error."""
        orch = _FakeOrch(
            error=DBProxyError("gone", code=ConfirmationNotFoundError.code)
        )
        handler, server = self._handler(orch)
        response = await handler(
            ConfirmRequest(request_id="req-1", action_id="act-1", decision="approve")
        )
        assert response.accepted is False
        assert response.status == "unknown"
        assert server.events == []

    @pytest.mark.parametrize(
        "error",
        [
            DBProxyError("boom"),
            RuntimeError("core down"),
        ],
        ids=["other_db_error", "runtime_error"],
    )
    async def test_other_errors_propagate(self, error: Exception):
        """Any other error propagates to the caller unswallowed."""
        orch = _FakeOrch(error=error)
        handler, _ = self._handler(orch)
        with pytest.raises(type(error)):
            await handler(
                ConfirmRequest(
                    request_id="req-1", action_id="act-1", decision="approve"
                )
            )

    async def test_missing_outcome_is_a_runtime_error(self):
        """A response without a typed outcome is a programming error."""
        orch = _FakeOrch(
            core_response=SimpleNamespace(outcome=None, structured_content=None)
        )
        handler, _ = self._handler(orch)
        with pytest.raises(RuntimeError):
            await handler(
                ConfirmRequest(
                    request_id="req-1", action_id="act-1", decision="approve"
                )
            )


# --- Run-events replay handler ----------------------------------------------


class TestListRunEventsHandler:
    """The reconnect replay used by FastAPI after a stream drops."""

    async def test_run_id_returns_filtered_events_from_the_turn(self):
        """Events of a known run are filtered by sequence and limited."""
        turn = {
            "events": [
                {"sequence": 1, "kind": "a"},
                {"sequence": 2, "kind": "b"},
                "junk",
                {"sequence": 3, "kind": "c"},
                {"sequence": 5, "kind": "d"},
            ]
        }
        db = _FakeDBClient(turn=turn)
        handler = _build_list_run_events_handler(db)  # type: ignore[arg-type]
        events = await handler(
            ListAgentRunEventsRequest(
                request_id="r1", run_id="run-1", after_sequence=1, limit=2
            )
        )
        assert events == [
            {"sequence": 2, "kind": "b"},
            {"sequence": 3, "kind": "c"},
        ]

    async def test_unknown_run_without_conversation_falls_back(self):
        """An unknown run without a conversation uses the plain query."""
        db = _FakeDBClient(turn=None, runtime_events=[{"sequence": 9}])
        handler = _build_list_run_events_handler(db)  # type: ignore[arg-type]
        events = await handler(
            ListAgentRunEventsRequest(request_id="r1", run_id="ghost")
        )
        assert events == [{"sequence": 9}]
        assert db.fallback_calls[0]["run_id"] == "ghost"

    async def test_conversation_merges_core_and_runtime_in_order(self):
        """A conversation replay merges, orders, and trims the events."""
        core = [
            {"created_at": "2026-08-01T10:00:02", "run_id": "r2", "sequence": 1},
            {"created_at": "2026-08-01T10:00:01", "run_id": "r1", "sequence": 2},
        ]
        runtime = [
            {"created_at": "2026-08-01T10:00:01", "run_id": "r1", "sequence": 1},
            {"created_at": "2026-08-01T10:00:03", "run_id": "r3", "sequence": 5},
        ]
        db = _FakeDBClient(core_events=core, runtime_events=runtime)
        handler = _build_list_run_events_handler(db)  # type: ignore[arg-type]
        events = await handler(
            ListAgentRunEventsRequest(
                request_id="r1", conversation_id="conv-1", limit=2
            )
        )
        assert events == [
            {"created_at": "2026-08-01T10:00:02", "run_id": "r2", "sequence": 1},
            {"created_at": "2026-08-01T10:00:03", "run_id": "r3", "sequence": 5},
        ]

    async def test_no_run_no_conversation_is_a_plain_query(self):
        """No selectors means a plain passthrough query."""
        db = _FakeDBClient(runtime_events=[{"sequence": 1}])
        handler = _build_list_run_events_handler(db)  # type: ignore[arg-type]
        await handler(ListAgentRunEventsRequest(request_id="r1", limit=5))
        call = db.fallback_calls[0]
        assert call["run_id"] is None
        assert call["conversation_id"] is None
        assert call["limit"] == 5


# --- Memory event forwarder --------------------------------------------------


class TestForwardMemoryEvents:
    """The bridge from the in-process memory bus into IPC streams."""

    async def test_event_is_forwarded_as_a_memory_event(self):
        """A bus event becomes one MEMORY_EVENT stream push."""
        bus = _FakeMemoryBus()
        server = _FakeIPCServer()
        task = asyncio.create_task(
            _forward_memory_events_to_ipc(
                memory_event_bus=bus,  # type: ignore[arg-type]
                ipc_server=server,  # type: ignore[arg-type]
            )
        )
        await bus.queue.put({"kind": "remember", "id": "m1"})
        for _ in range(100):
            if server.events:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await task
        assert server.events[0].request_id == "memory_debug"
        assert server.events[0].event_type == StreamEventType.MEMORY_EVENT
        assert server.events[0].data == {"kind": "remember", "id": "m1"}

    async def test_unsubscribes_on_cancellation(self):
        """The bus subscription is released when the forwarder stops."""
        bus = _FakeMemoryBus()
        server = _FakeIPCServer()
        task = asyncio.create_task(
            _forward_memory_events_to_ipc(
                memory_event_bus=bus,  # type: ignore[arg-type]
                ipc_server=server,  # type: ignore[arg-type]
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        await task
        assert bus.unsubscribed == ["sub-1"]

    async def test_failed_push_does_not_stop_the_forwarder(self):
        """A transient IPC failure drops one event, the loop continues."""
        bus = _FakeMemoryBus()
        server = _FakeIPCServer(fail_first_push_with=IPCChannelError("closed"))
        task = asyncio.create_task(
            _forward_memory_events_to_ipc(
                memory_event_bus=bus,  # type: ignore[arg-type]
                ipc_server=server,  # type: ignore[arg-type]
            )
        )
        await bus.queue.put({"kind": "dropped"})
        await bus.queue.put({"kind": "kept"})
        for _ in range(100):
            if len(server.events) == 1:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await task
        assert [event.data["kind"] for event in server.events] == ["kept"]


# --- Background task bookkeeping ---------------------------------------------


class TestBackgroundTaskHelpers:
    """Tracking, cancelling, and closing runtime helper objects."""

    async def test_register_appends_the_task(self):
        """A registered task is appended to the tracking list."""
        tasks: list[asyncio.Task] = []
        task = asyncio.create_task(asyncio.sleep(30))
        _register_background_task(
            tasks=tasks,
            task=task,
            log_message="started {name}",
            log_value="worker",
        )
        assert tasks == [task]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.parametrize(
        "client",
        [
            None,
            SimpleNamespace(),
            SimpleNamespace(close=lambda: None),
        ],
        ids=["none", "no_close", "sync_close"],
    )
    async def test_close_llm_client_tolerates_missing_close(self, client: object):
        """A None client or a client without close is a no-op."""
        await _close_llm_client(client)

    async def test_close_llm_client_awaits_async_close(self):
        """An async close coroutine is awaited."""
        closed: list[str] = []

        async def close() -> None:
            closed.append("closed")

        await _close_llm_client(SimpleNamespace(close=close))
        assert closed == ["closed"]

    async def test_cancel_background_tasks_cancels_and_awaits(self):
        """Every tracked task is cancelled and awaited without raising."""

        async def long_running() -> None:
            await asyncio.sleep(30)

        first = asyncio.create_task(long_running())
        second = asyncio.create_task(long_running())
        await _cancel_background_tasks([first, second])
        assert first.cancelled()
        assert second.cancelled()


class TestConfirmErrorTypes:
    """The typed IPC error classes behind the confirm handler."""

    def test_confirmation_not_found_is_a_value_error(self) -> None:
        """The typed error keeps its historical ValueError compatibility."""
        assert issubclass(ConfirmationNotFoundError, ValueError)
        error = ConfirmationNotFoundError("confirmation 'x' not found")
        assert error.code == "confirmation_unknown"
        assert str(error) == "confirmation 'x' not found"

    def test_operation_not_found_keeps_plain_exception_base(self) -> None:
        """Operation errors stay plain exceptions; only the code is the contract."""
        error = OperationNotFoundError("operation 'x' not found")
        assert error.code == "operation_unknown"
        assert not isinstance(error, ValueError)
