"""Strict LLM classification tests for bound read-only permission replies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.agent.llm_caller import LLMCaller
from backend.ipc.models import ChatRequest
from backend.llm.call_factory import LLMResponse
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.usage import LLMCallUsage
from backend.permissions import ActionDecisionService
from backend.processes.orchestrator.chat_handler import (
    _maybe_handle_permission_reply,
    _run_chat_request_with_prepared_message,
)


async def test_permission_reply_matrix() -> None:
    """Valid multilingual decisions pass; malformed output retries then is unclear."""
    caller = LLMCaller("test", None, None, MagicMock())
    caller.invoke = AsyncMock(
        side_effect=[
            SimpleNamespace(text='{"decision":"approved"}', usage=None),
            SimpleNamespace(text="explanation", usage=None),
            SimpleNamespace(text='{"decision":"rejected"}', usage=None),
            SimpleNamespace(text="yes", usage=None),
            SimpleNamespace(text="still yes", usage=None),
        ]
    )
    common = {
        "tool_name": "http_fetch",
        "scope": {"conversation_id": "conversation-1"},
        "llm_call": AsyncMock(),
        "call_usages": [],
    }
    assert await caller.classify_permission_reply(reply="да", **common) == "approved"
    assert await caller.classify_permission_reply(reply="нет", **common) == "rejected"
    assert await caller.classify_permission_reply(reply="maybe", **common) == "unclear"
    assert caller.invoke.await_count == 5
    for invocation in caller.invoke.await_args_list:
        assert invocation.kwargs["response_format"].value == "json_schema"
        assert invocation.kwargs["response_schema"]["additionalProperties"] is False

    request = ChatRequest(
        request_id="request-1",
        message="да",
        channel="web",
        session_id="session-1",
        conversation_id="conversation-1",
    )
    classifier = AsyncMock()
    orch = SimpleNamespace(
        tool_executor=SimpleNamespace(action_decisions=ActionDecisionService()),
        llm_caller=SimpleNamespace(classify_permission_reply=classifier),
    )
    for pending in ([], [{"confirmation_id": "one"}, {"confirmation_id": "two"}]):
        db_client = SimpleNamespace(
            list_pending_agent_confirmations=AsyncMock(return_value=pending)
        )
        assert (
            await _maybe_handle_permission_reply(
                request=request,
                orch=orch,
                db_client=db_client,
                llm_call=AsyncMock(),
                run_event_context=None,
            )
            is None
        )
    sensitive = {
        "confirmation_id": "sensitive-1",
        "turn_id": "turn-1",
        "channel": "web",
        "tool_name": "email_send",
        "arguments": {"to": "user@example.test", "body": "hello"},
    }
    db_client = SimpleNamespace(
        list_pending_agent_confirmations=AsyncMock(return_value=[sensitive])
    )
    assert (
        await _maybe_handle_permission_reply(
            request=request,
            orch=orch,
            db_client=db_client,
            llm_call=AsyncMock(),
            run_event_context=None,
        )
        is None
    )
    classifier.assert_not_awaited()


async def test_permission_classifier_sends_custom_schema_without_tools() -> None:
    """The real invoke boundary carries the strict enum schema to the provider."""
    caller = LLMCaller("test", None, None, MagicMock())
    captured = {}

    async def llm_call(messages, *, options):
        captured["messages"] = messages
        captured["options"] = options
        return LLMResponse(text='{"decision":"approved"}')

    decision = await caller.classify_permission_reply(
        reply="да",
        tool_name="web_search",
        scope={"conversation_id": "conversation-1"},
        llm_call=llm_call,
        call_usages=[],
    )
    assert decision == "approved"
    assert captured["options"].response_format is LlmResponseFormat.JSON_SCHEMA
    assert captured["options"].response_schema["required"] == ["decision"]
    assert captured["options"].tools == []
    assert captured["options"].thinking_enabled is False


@pytest.mark.parametrize(
    ("decision", "resume_decision"),
    [("approved", "approved"), ("rejected", "denied")],
)
async def test_sole_read_only_permission_reply_resumes_bound_confirmation(
    monkeypatch,
    decision: str,
    resume_decision: str,
) -> None:
    """Only the sole reusable read-only card reaches the canonical resume CAS."""
    confirmation = {
        "confirmation_id": "confirmation-1",
        "turn_id": "turn-1",
        "channel": "web",
        "tool_name": "web_search",
        "arguments": {"query": "btc"},
    }
    classifier = AsyncMock(return_value=decision)
    resume = AsyncMock(return_value=MagicMock())
    orch = SimpleNamespace(
        tool_executor=SimpleNamespace(action_decisions=ActionDecisionService()),
        llm_caller=SimpleNamespace(
            classify_permission_reply=classifier,
            model_name="test-model",
        ),
        resume_structured_confirmation=resume,
        _flush_provider_attempts=AsyncMock(),
    )
    db_client = SimpleNamespace(
        list_pending_agent_confirmations=AsyncMock(return_value=[confirmation])
    )
    expected = MagicMock()
    monkeypatch.setattr(
        "backend.processes.orchestrator.chat_handler._build_chat_response",
        lambda **_kwargs: expected,
    )
    provider_call = AsyncMock()
    response = await _maybe_handle_permission_reply(
        request=ChatRequest(
            request_id="request-1",
            message="да" if decision == "approved" else "нет",
            channel="web",
            session_id="session-1",
            conversation_id="conversation-1",
        ),
        orch=orch,
        db_client=db_client,
        llm_call=provider_call,
        run_event_context=None,
    )
    assert response is expected
    resume.assert_awaited_once_with(
        confirmation_id="confirmation-1",
        decision=resume_decision,
        llm_call=provider_call,
        preliminary_call_usages=[],
    )


async def test_one_reply_resolves_compatible_web_search_group(monkeypatch) -> None:
    """One chat reply grants every waiting call of the same reusable tool."""
    confirmations = [
        {
            "confirmation_id": f"confirmation-{index}",
            "turn_id": "turn-1",
            "channel": "web",
            "tool_name": "web_search",
            "arguments": {"query": query},
        }
        for index, query in enumerate(("coingecko", "KASPA price", "BTC price"), 1)
    ]
    classifier = AsyncMock(return_value="approved")
    responses = [MagicMock(), MagicMock(), MagicMock()]
    resume = AsyncMock(side_effect=responses)
    orch = SimpleNamespace(
        tool_executor=SimpleNamespace(action_decisions=ActionDecisionService()),
        llm_caller=SimpleNamespace(
            classify_permission_reply=classifier,
            model_name="test-model",
        ),
        resume_structured_confirmation=resume,
        _flush_provider_attempts=AsyncMock(),
    )
    db_client = SimpleNamespace(
        list_pending_agent_confirmations=AsyncMock(return_value=confirmations)
    )
    expected = MagicMock()
    monkeypatch.setattr(
        "backend.processes.orchestrator.chat_handler._build_chat_response",
        lambda **_kwargs: expected,
    )
    provider_call = AsyncMock()

    result = await _maybe_handle_permission_reply(
        request=ChatRequest(
            request_id="request-1",
            message="да",
            channel="web",
            session_id="session-1",
            conversation_id="conversation-1",
        ),
        orch=orch,
        db_client=db_client,
        llm_call=provider_call,
        run_event_context=None,
    )

    assert result is expected
    classifier.assert_awaited_once()
    assert [
        call.kwargs["confirmation_id"] for call in resume.await_args_list
    ] == ["confirmation-1", "confirmation-2", "confirmation-3"]
    assert all(
        call.kwargs["decision"] == "approved" for call in resume.await_args_list
    )
    assert resume.await_args_list[0].kwargs["preliminary_call_usages"] == []
    assert resume.await_args_list[-1].kwargs["llm_call"] is provider_call


async def test_fresh_http_run_id_does_not_bypass_permission_classifier(
    monkeypatch,
) -> None:
    """FastAPI's fresh request UUID must not turn an approval into a new turn."""
    expected = MagicMock()
    classify = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "backend.processes.orchestrator.chat_handler._maybe_handle_permission_reply",
        classify,
    )
    create_turn = AsyncMock()
    request = ChatRequest(
        request_id="request-1",
        run_id="fresh-http-uuid",
        message="разрешаю",
        channel="web",
        session_id="session-1",
        conversation_id="conversation-1",
    )
    response = await _run_chat_request_with_prepared_message(
        request=request,
        orch=SimpleNamespace(),
        db_client=SimpleNamespace(
            get_agent_turn=AsyncMock(return_value=None),
            create_agent_turn=create_turn,
        ),
        broker=SimpleNamespace(),
        llm_call=AsyncMock(),
        stt_client=None,
    )
    assert response is expected
    classify.assert_awaited_once()
    create_turn.assert_not_awaited()


async def test_unclear_permission_reply_keeps_card_and_reports_usage() -> None:
    """Unclear classification mutates nothing and includes its provider usage."""
    usage = LLMCallUsage(
        prompt_tokens=10,
        completion_tokens=2,
        model="test-model",
        phase="Permission classifier",
    )

    async def classify(*, call_usages, **_kwargs):
        call_usages.append(usage)
        return "unclear"

    confirmation = {
        "confirmation_id": "confirmation-1",
        "turn_id": "turn-1",
        "channel": "web",
        "tool_name": "web_search",
        "arguments": {"query": "btc"},
    }
    canonical = {
        "turn_id": "turn-1",
        "terminal_outcome": "paused",
        "finalization_id": "final-1",
        "confirmations": [
            {**confirmation, "status": "pending", "parameters": {"query": "btc"}}
        ],
        "pending_inputs": [],
    }
    resume = AsyncMock()
    orch = SimpleNamespace(
        tool_executor=SimpleNamespace(action_decisions=ActionDecisionService()),
        llm_caller=SimpleNamespace(
            classify_permission_reply=classify,
            model_name="test-model",
        ),
        resume_structured_confirmation=resume,
        _flush_provider_attempts=AsyncMock(),
    )
    db_client = SimpleNamespace(
        list_pending_agent_confirmations=AsyncMock(return_value=[confirmation]),
        get_agent_turn=AsyncMock(return_value=canonical),
    )
    response = await _maybe_handle_permission_reply(
        request=ChatRequest(
            request_id="request-1",
            message="maybe",
            channel="web",
            session_id="session-1",
            conversation_id="conversation-1",
        ),
        orch=orch,
        db_client=db_client,
        llm_call=AsyncMock(),
        run_event_context=None,
    )
    assert response.outcome_kind.value == "paused"
    assert response.stats.total_tokens == 12
    resume.assert_not_awaited()
