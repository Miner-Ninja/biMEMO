"""Required RED tool confirmation continuation tests."""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.concurrency import AgentConcurrency
from backend.tools.broker import ToolBroker, ToolResult
from backend.tools.handlers.registration import register_tool_definitions
from backend.tools.registry import ToolRegistry


_EMAIL_ARGUMENTS = {
    "to": "a@example.test",
    "subject": "Status",
    "body": "Ready",
}
_EMAIL_CANONICAL = json.dumps(
    _EMAIL_ARGUMENTS,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
_EMAIL_HASH = hashlib.sha256(_EMAIL_CANONICAL.encode("utf-8")).hexdigest()


def _canonical_confirmation(*, status: str = "pending") -> dict[str, object]:
    """Build the authoritative confirmation row required by resume."""
    return {
        "confirmation_id": "confirmation-1",
        "turn_id": "turn-1",
        "invocation_id": "invocation-1",
        "tool_name": "email_send",
        "parameters": dict(_EMAIL_ARGUMENTS),
        "status": status,
    }


def _registry_broker(*, result: ToolResult) -> ToolBroker:
    """Build a finalized registry broker with a mocked dispatch boundary."""
    registry = ToolRegistry()
    register_tool_definitions(registry)
    broker = ToolBroker(registry)

    async def unused_handler(_parameters: object) -> None:
        return None

    for tool_name in registry.tool_names:
        handler = (
            AsyncMock(return_value=result.result)
            if tool_name == result.tool_name
            else unused_handler
        )
        broker.bind_handler(tool_name, handler)
        if tool_name == result.tool_name:
            broker.test_handler = handler
    broker.finalize()
    return broker


async def test_denial_refreshes_canonical_confirmation_state() -> None:
    """A denied card is not replayed from the pre-decision snapshot."""
    turn_store = AsyncMock()
    turn_store.get_agent_confirmation.return_value = _canonical_confirmation()
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
        "tool_policy_version": "registry-v1",
        "terminal_outcome": None,
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_name": "email_send",
                "arguments": dict(_EMAIL_ARGUMENTS),
                "canonical_args_hash": _EMAIL_HASH,
                "policy_revision": "registry-v1",
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


async def test_confirmed_oversized_result_fails_before_success_bind() -> None:
    """Confirmation resume cannot bind an envelope that had to be truncated."""
    turn_store = AsyncMock()
    turn_store.get_agent_confirmation.return_value = _canonical_confirmation()
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
        "tool_policy_version": "registry-v1",
        "terminal_outcome": None,
        "confirmations": [],
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_name": "email_send",
                "arguments": dict(_EMAIL_ARGUMENTS),
                "canonical_args_hash": _EMAIL_HASH,
                "policy_revision": "registry-v1",
                "status": "pending_confirmation",
            }
        ],
    }
    turn_store.authorize_agent_invocation.return_value = {
        "claimed": True,
        "claim_generation": 1,
    }
    broker = _registry_broker(
        result=ToolResult(
            tool_name="email_send",
            success=True,
            result="x" * 70_000,
            duration_ms=1.0,
        ),
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
    turn_store.get_agent_confirmation.return_value = _canonical_confirmation()
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
        "tool_policy_version": "registry-v1",
        "terminal_outcome": None,
        "active_time_ms": 10**9,
        "confirmations": [],
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_name": "email_send",
                "arguments": dict(_EMAIL_ARGUMENTS),
                "canonical_args_hash": _EMAIL_HASH,
                "policy_revision": "registry-v1",
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
