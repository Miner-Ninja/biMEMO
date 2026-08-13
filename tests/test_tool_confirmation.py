"""Required RED tool confirmation continuation tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.agent_loop_models import ToolCall
from backend.agent.concurrency import AgentConcurrency
from backend.agent.tool_executor import ToolExecutor
from backend.processes.orchestrator.process import _resume_llm_after_confirmed_action
from backend.tools.broker import ToolResult
from backend.trust.confirmation import ConfirmationManager
from backend.trust.zones import TrustZone


def _executor(*, recorder=None) -> ToolExecutor:
    """Build a tool executor for confirmation queue tests."""
    return ToolExecutor(
        MagicMock(),
        ConfirmationManager(),
        pending_action_recorder=recorder,
    )


async def test_required_tools_are_persisted_with_continuation() -> None:
    """The persisted resume snapshot retains the original final gate."""
    recorder = AsyncMock()
    executor = _executor(recorder=recorder)
    executor.current_context = {"agent_run_id": "run-1"}

    await executor.queue_red_tool_confirmations(
        [ToolCall("call-1", "email_send", {"to": "a@example.com"})],
        "web",
        messages=[{"role": "user", "content": "send"}],
        required_tools=("email_send", "git_status"),
    )

    snapshot = recorder.await_args.kwargs["resume_snapshot"]
    assert snapshot["required_tools"] == ["email_send", "git_status"]
    assert executor.executed_tools[-1]["status"] == "pending"
    assert executor.executed_tools[-1]["success"] is False


async def test_same_invocation_is_queued_once() -> None:
    """Retrying the same call ID does not create a duplicate confirmation."""
    executor = _executor()
    calls = [ToolCall("call-1", "email_send", {"to": "a@example.com"})]

    await executor.queue_red_tool_confirmations(calls, "web")
    await executor.queue_red_tool_confirmations(calls, "web")

    assert len(executor.pending_red_calls) == 1
    assert executor.confirmation_mgr.pending_count == 1


async def test_different_invocation_cannot_overwrite_pending_continuation() -> None:
    """A second call cannot replace a continuation awaiting a decision."""
    executor = _executor()
    await executor.queue_red_tool_confirmations(
        [ToolCall("call-1", "email_send", {"to": "a@example.com"})],
        "web",
    )

    with pytest.raises(RuntimeError, match="already pending"):
        await executor.queue_red_tool_confirmations(
            [ToolCall("call-2", "email_send", {"to": "b@example.com"})],
            "web",
        )

    assert len(executor.pending_red_calls) == 1
    assert executor.confirmation_mgr.pending_count == 1


async def test_decided_action_is_consumed_from_active_pending_ledger() -> None:
    """Consuming one decision leaves other batch actions pending."""
    executor = _executor()
    await executor.queue_red_tool_confirmations(
        [
            ToolCall("call-1", "email_send", {"to": "a@example.com"}),
            ToolCall("call-2", "file_delete", {"path": "/tmp/x"}),
        ],
        "web",
    )
    first_action_id = executor.pending_red_calls[0].action_id

    executor.consume_pending(first_action_id)

    assert len(executor.pending_red_calls) == 1
    assert executor.pending_red_calls[0].action_id != first_action_id


async def test_denial_refreshes_canonical_confirmation_state() -> None:
    """A denied card is not replayed from the pre-decision snapshot."""
    turn_store = AsyncMock()
    turn_store.get_agent_confirmation.return_value = None
    turn_store.decide_agent_confirmation.return_value = {
        "turn_id": "turn-1",
        "invocation_id": "invocation-1",
        "status": "denied",
        "changed": True,
    }
    base = {
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "session_id": "session-1",
        "channel": "web",
        "terminal_outcome": None,
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_name": "email_send",
                "arguments": {"to": "a@example.test"},
                "status": "pending_confirmation",
            }
        ],
    }
    turn_store.get_agent_turn.side_effect = [
        {
            **base,
            "confirmations": [
                {
                    "confirmation_id": "confirmation-1",
                    "status": "pending",
                }
            ],
        },
        {**base, "confirmations": []},
    ]
    orchestrator = AgentOrchestrator(
        broker=MagicMock(),
        concurrency=AgentConcurrency(),
        context_fn=lambda _session_id, **_kwargs: [],
        turn_store=turn_store,
    )
    orchestrator._restore_structured_preparation = MagicMock()
    expected = MagicMock()
    orchestrator._commit_structured_failure = AsyncMock(return_value=expected)

    response = await orchestrator.resume_structured_confirmation(
        confirmation_id="confirmation-1",
        decision="denied",
        llm_call=AsyncMock(),
    )

    assert response is expected
    assert (
        orchestrator._commit_structured_failure.await_args.kwargs["code"]
        == "tool_confirmation_denied"
    )


async def test_resume_blocks_synthesis_when_another_required_tool_is_missing() -> None:
    """Approval cannot bypass other requirements from the original turn."""
    llm_call = AsyncMock()
    answer = await _resume_llm_after_confirmed_action(
        llm_call=llm_call,
        resume_context={
            "messages": [],
            "assistant_message": {},
            "tool_call_id": "call-1",
            "required_tools": ["email_send", "git_status"],
        },
        tool_result_text="sent",
        completed_tool_name="email_send",
    )

    assert "git_status" in answer
    llm_call.assert_not_awaited()


async def test_confirmed_oversized_result_fails_before_success_bind() -> None:
    """Confirmation resume cannot bind an envelope that had to be truncated."""
    turn_store = AsyncMock()
    turn_store.decide_agent_confirmation.return_value = {
        "turn_id": "turn-1",
        "invocation_id": "invocation-1",
        "status": "approved",
        "changed": True,
    }
    turn_store.get_agent_turn.return_value = {
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "session_id": "session-1",
        "channel": "web",
        "terminal_outcome": None,
        "confirmations": [],
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_name": "email_send",
                "arguments": {"to": "a@example.test"},
                "status": "pending_confirmation",
            }
        ],
    }
    turn_store.claim_agent_invocation.return_value = {
        "claimed": True,
        "claim_generation": 1,
    }
    broker = MagicMock()
    broker.execute = AsyncMock(
        return_value=ToolResult(
            tool_name="email_send",
            success=True,
            result="x" * 70_000,
            zone=TrustZone.RED,
            duration_ms=1.0,
        )
    )
    orchestrator = AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda _session_id, **_kwargs: [],
        turn_store=turn_store,
    )
    prepared = MagicMock()
    orchestrator._restore_structured_preparation = MagicMock(return_value=prepared)
    failure = MagicMock()
    orchestrator._commit_structured_failure = AsyncMock(return_value=failure)

    response = await orchestrator.resume_structured_confirmation(
        confirmation_id="confirmation-1",
        decision="approved",
        llm_call=AsyncMock(),
    )

    assert response is failure
    completed = turn_store.complete_agent_invocation.await_args.kwargs
    assert completed["status"] == "failed"
    assert completed["result"] is None
    assert "exceeded" in completed["error"]
    orchestrator._commit_structured_failure.assert_awaited_once()


async def test_confirmed_action_checks_deadline_before_dispatch() -> None:
    """An approved RED action cannot dispatch after its active budget expired."""
    turn_store = AsyncMock()
    turn_store.decide_agent_confirmation.return_value = {
        "turn_id": "turn-1",
        "invocation_id": "invocation-1",
        "status": "approved",
        "changed": True,
    }
    turn_store.get_agent_turn.return_value = {
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "session_id": "session-1",
        "channel": "web",
        "terminal_outcome": None,
        "active_time_ms": 10**9,
        "confirmations": [],
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_name": "email_send",
                "arguments": {"to": "a@example.test"},
                "status": "pending_confirmation",
            }
        ],
    }
    broker = MagicMock()
    broker.execute = AsyncMock()
    orchestrator = AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda _session_id, **_kwargs: [],
        turn_store=turn_store,
    )
    orchestrator._restore_structured_preparation = MagicMock(return_value=MagicMock())
    failure = MagicMock()
    orchestrator._commit_structured_failure = AsyncMock(return_value=failure)

    response = await orchestrator.resume_structured_confirmation(
        confirmation_id="confirmation-1",
        decision="approved",
        llm_call=AsyncMock(),
    )

    assert response is failure
    turn_store.claim_agent_invocation.assert_not_awaited()
    broker.execute.assert_not_awaited()
    assert (
        orchestrator._commit_structured_failure.await_args.kwargs["code"]
        == "active_deadline_exhausted"
    )


async def test_approved_before_claim_replays_after_restart() -> None:
    """A durable approval resumes when the process stopped before its claim."""
    turn_store = AsyncMock()
    turn_store.decide_agent_confirmation.return_value = {
        "turn_id": "turn-1",
        "invocation_id": "invocation-1",
        "status": "approved",
        "changed": False,
    }
    canonical = {
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "session_id": "session-1",
        "channel": "web",
        "terminal_outcome": None,
        "active_time_ms": 0,
        "confirmations": [
            {
                "confirmation_id": "confirmation-1",
                "invocation_id": "invocation-1",
                "status": "approved",
            }
        ],
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_name": "email_send",
                "arguments": {"to": "a@example.test"},
                "status": "pending_confirmation",
            }
        ],
    }
    completed = {
        **canonical,
        "invocations": [{**canonical["invocations"][0], "status": "succeeded"}],
    }
    turn_store.get_agent_turn.side_effect = [canonical, completed]
    turn_store.claim_agent_invocation.return_value = {
        "claimed": True,
        "claim_generation": 1,
    }
    broker = MagicMock()
    broker.execute = AsyncMock(
        return_value=ToolResult(
            tool_name="email_send",
            success=True,
            result="sent",
            zone=TrustZone.RED,
            duration_ms=1.0,
        )
    )
    orchestrator = AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda _session_id, **_kwargs: [],
        turn_store=turn_store,
    )
    prepared = MagicMock()
    orchestrator._restore_structured_preparation = MagicMock(return_value=prepared)
    expected = MagicMock()
    orchestrator._run_structured_authority_turn = AsyncMock(return_value=expected)

    response = await orchestrator.resume_structured_confirmation(
        confirmation_id="confirmation-1",
        decision="approved",
        llm_call=AsyncMock(),
    )

    assert response is expected
    turn_store.claim_agent_invocation.assert_awaited_once_with(
        turn_id="turn-1",
        invocation_id="invocation-1",
        idempotency_key="turn-1:invocation-1",
        expected_statuses=["pending_confirmation"],
    )
    broker.execute.assert_awaited_once()
