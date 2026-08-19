"""Core-authority tests for typed semantic missing-input pauses."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.agent_loop_models import PreparedContext, TurnPreparation
from backend.agent.finalization import RequirementSatisfaction, ResponseRequirements
from backend.agent.loop import AgenticLoop
from backend.agent.turn_state import ObligationStatus, ToolObligation
from backend.llm.call_factory import LLMResponse
from backend.llm.capabilities import LlmResponseFormat
from backend.memory.classification.question_router import route_memory_question
from backend.processes.db_proxy.handlers.agent_turn import (
    make_append_agent_turn_event_handler,
    make_create_agent_pending_input_handler,
    make_create_agent_turn_handler,
    make_get_agent_pending_input_handler,
    make_get_agent_turn_handler,
    make_reply_agent_pending_input_handler,
    make_upsert_agent_turn_obligations_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


def _open_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "pending-input.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    return db


async def test_pending_question_is_json_formatted_with_suggestions() -> None:
    """Every visible missing-input question uses the structured formatter."""
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.turn_store = AsyncMock()
    orchestrator.turn_store.get_agent_turn.return_value = {
        "tool_rounds": 1,
        "pending_inputs": [],
    }
    orchestrator.turn_store.create_agent_pending_input.return_value = {
        "pending_input_id": "input-1",
        "question": "What should I help with?",
        "suggestions": ["Find the Bitcoin price", "Check system status"],
        "expected_input_schema": {"type": "string"},
        "status": "pending",
        "expires_at": "2999-01-01T00:00:00+00:00",
        "cas_version": 1,
    }
    orchestrator.prompt_builder = SimpleNamespace(suggestions_enabled=True)
    orchestrator.tool_executor = SimpleNamespace(executed_tools=[])
    orchestrator._provider_attempt_tasks = []
    orchestrator._require_structured_capabilities = AsyncMock(
        return_value=SimpleNamespace(max_context_tokens=262_144)
    )
    llm_call = AsyncMock(
        return_value=LLMResponse(
            text=(
                '{"answer":"What should I help with?","suggestions":'
                '["Find the Bitcoin price","Check system status"]}'
            )
        )
    )
    preparation = TurnPreparation(
        filtered_message="привет",
        input_filtered=False,
        request_started_at=__import__("time").perf_counter(),
        call_usages=[],
        prepared_context=PreparedContext(
            entries=[],
            retrieved_memories=[],
            retrieved_messages=None,
            memory_guidance=None,
        ),
        user_message_id=None,
        messages=[{"role": "user", "content": "привет"}],
        openai_tools=[],
        question_route=route_memory_question(""),
        requirements=ResponseRequirements(),
        satisfaction=RequirementSatisfaction(),
        run_id="turn-1",
        finalization_id="final-1",
    )
    loop = AgenticLoop(injection_flag=False)
    loop.start()

    response = await orchestrator._pause_for_missing_input(
        prepared_turn=preparation,
        loop=loop,
        llm_call=llm_call,
        session_id="session-1",
        conversation_id="conversation-1",
        question="What should I help with?",
        expected_input_schema={"type": "string"},
        assistant_message={"role": "assistant", "tool_calls": []},
    )

    assert llm_call.await_args.kwargs["options"].response_format is (
        LlmResponseFormat.JSON_SCHEMA
    )
    assert llm_call.await_args.kwargs["phase_override"] == "Pending input"
    assert response.outcome is not None
    assert response.outcome.pending_input is not None
    assert response.outcome.pending_input.suggestions == (
        "Find the Bitcoin price",
        "Check system status",
    )


async def test_tool_control_migration(tmp_path) -> None:
    """A valid reply resumes once; invalid and stale replies do not mutate it."""
    db = _open_db(tmp_path)
    await make_create_agent_turn_handler(db)(
        {
            "turn_id": "turn-1",
            "finalization_id": "final-1",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "channel": "web",
        }
    )
    create = make_create_agent_pending_input_handler(db)
    await create(
        {
            "turn_id": "turn-1",
            "pending_input_id": "input-1",
            "question": "How many?",
            "suggestions": ["Use 5", "Use 10"],
            "expected_input_schema": {"type": "integer"},
            "transcript_snapshot": [{"role": "user", "content": "schedule it"}],
            "expires_at": "2999-01-01T00:00:00+00:00",
        }
    )

    reply = make_reply_agent_pending_input_handler(db)
    invalid = await reply(
        {
            "turn_id": "turn-1",
            "pending_input_id": "input-1",
            "conversation_id": "conversation-1",
            "cas_version": 1,
            "value": "not-a-number",
        }
    )
    assert invalid["validation_error"] == "pending input requires an integer"
    pending = (
        await make_get_agent_pending_input_handler(db)({"pending_input_id": "input-1"})
    )["pending_input"]
    assert pending["status"] == "pending"
    assert pending["cas_version"] == 1
    assert pending["suggestions"] == ["Use 5", "Use 10"]

    answered = (
        await reply(
            {
                "turn_id": "turn-1",
                "pending_input_id": "input-1",
                "conversation_id": "conversation-1",
                "cas_version": 1,
                "value": "7",
            }
        )
    )["pending_input"]
    assert answered["status"] == "answered"
    assert answered["answer"] == 7
    assert answered["cas_version"] == 2
    with pytest.raises(ValueError, match="stale or already decided"):
        await reply(
            {
                "turn_id": "turn-1",
                "pending_input_id": "input-1",
                "conversation_id": "conversation-1",
                "cas_version": 1,
                "value": "8",
            }
        )
    db.close()


async def test_pending_input_is_conversation_bound_and_expiry_is_terminal(
    tmp_path,
) -> None:
    """Another chat cannot answer; expiry atomically commits canonical FAILED."""
    db = _open_db(tmp_path)
    await make_create_agent_turn_handler(db)(
        {
            "turn_id": "turn-expired",
            "finalization_id": "final-expired",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "channel": "web",
        }
    )
    await make_create_agent_pending_input_handler(db)(
        {
            "turn_id": "turn-expired",
            "pending_input_id": "input-expired",
            "question": "Which value?",
            "expected_input_schema": {"type": "string"},
            "transcript_snapshot": [{"role": "user", "content": "continue"}],
            "expires_at": "2000-01-01T00:00:00+00:00",
        }
    )
    reply = make_reply_agent_pending_input_handler(db)
    with pytest.raises(ValueError, match="unknown pending input"):
        await reply(
            {
                "turn_id": "turn-expired",
                "pending_input_id": "input-expired",
                "conversation_id": "conversation-2",
                "cas_version": 1,
                "value": "secret",
            }
        )

    turn = (await make_get_agent_turn_handler(db)({"turn_id": "turn-expired"}))["turn"]
    assert turn["terminal_outcome"] == "failed"
    assert turn["outcome_payload"]["error"]["code"] == "pending_input_expired"
    assert turn["pending_inputs"][0]["status"] == "expired"
    assert (
        db.execute(
            "SELECT COUNT(*) FROM agent_turn_finalizations WHERE turn_id = ?",
            ("turn-expired",),
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ? "
            "AND role = 'assistant'",
            ("conversation-1",),
        ).fetchone()[0]
        == 1
    )
    db.close()


async def test_expiry_during_reply_terminalizes_and_allows_later_pause(
    tmp_path,
) -> None:
    """The reply race commits FAILED; answered pauses do not block a later one."""
    db = _open_db(tmp_path)
    create_turn = make_create_agent_turn_handler(db)
    create_input = make_create_agent_pending_input_handler(db)
    await create_turn(
        {
            "turn_id": "turn-race",
            "finalization_id": "final-race",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "channel": "web",
        }
    )
    await create_input(
        {
            "turn_id": "turn-race",
            "pending_input_id": "input-race",
            "question": "Value?",
            "expected_input_schema": {"type": "string"},
            "transcript_snapshot": [],
            "expires_at": "2000-01-01T00:00:00+00:00",
        }
    )
    replay = await create_input(
        {
            "turn_id": "turn-race",
            "pending_input_id": "input-race",
            "question": "Value?",
            "expected_input_schema": {"type": "string"},
            "transcript_snapshot": [],
            "expires_at": "2000-01-01T00:00:00+00:00",
        }
    )
    assert replay["pending_input"]["pending_input_id"] == "input-race"
    with pytest.raises(ValueError, match="replay payload mismatch"):
        await create_input(
            {
                "turn_id": "turn-race",
                "pending_input_id": "input-race",
                "question": "Different?",
                "expected_input_schema": {"type": "string"},
                "transcript_snapshot": [],
                "expires_at": "2000-01-01T00:00:00+00:00",
            }
        )
    expired = await make_reply_agent_pending_input_handler(db)(
        {
            "turn_id": "turn-race",
            "pending_input_id": "input-race",
            "conversation_id": "conversation-1",
            "cas_version": 1,
            "value": "late",
        }
    )
    assert expired["pending_input"]["status"] == "expired"
    turn = (await make_get_agent_turn_handler(db)({"turn_id": "turn-race"}))["turn"]
    assert turn["terminal_outcome"] == "failed"

    await create_turn(
        {
            "turn_id": "turn-sequential",
            "finalization_id": "final-sequential",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "channel": "web",
        }
    )
    for index in (1, 2):
        stored = await create_input(
            {
                "turn_id": "turn-sequential",
                "pending_input_id": f"input-{index}",
                "question": f"Question {index}?",
                "expected_input_schema": {"type": "string"},
                "transcript_snapshot": [],
                "expires_at": "2999-01-01T00:00:00+00:00",
            }
        )
        assert stored["pending_input"]["pending_input_id"] == f"input-{index}"
        if index == 1:
            with pytest.raises(ValueError, match="already has a pending input"):
                await create_input(
                    {
                        "turn_id": "turn-sequential",
                        "pending_input_id": "input-sibling",
                        "question": "Sibling?",
                        "expected_input_schema": {"type": "string"},
                        "transcript_snapshot": [],
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    }
                )
            await make_reply_agent_pending_input_handler(db)(
                {
                    "turn_id": "turn-sequential",
                    "pending_input_id": "input-1",
                    "conversation_id": "conversation-1",
                    "cas_version": 1,
                    "value": "first",
                }
            )
    db.close()


async def test_durable_semantic_round_budget(tmp_path) -> None:
    """Pause/resume keeps one run's rounds and explicit obligation open."""
    db = _open_db(tmp_path)
    await make_create_agent_turn_handler(db)(
        {
            "turn_id": "turn-budget",
            "finalization_id": "final-budget",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "channel": "web",
        }
    )
    await make_upsert_agent_turn_obligations_handler(db)(
        {
            "turn_id": "turn-budget",
            "obligations": [
                {
                    "obligation_id": "obligation-web",
                    "required_tool": "web_search",
                    "source": "manual",
                }
            ],
        }
    )
    append = make_append_agent_turn_event_handler(db)
    for attempt in (1, 2):
        await append(
            {
                "turn_id": "turn-budget",
                "event_type": "tool_round_started",
                "phase": "tool_loop",
                "attempt": attempt,
                "payload": {},
            }
        )
    await make_create_agent_pending_input_handler(db)(
        {
            "turn_id": "turn-budget",
            "pending_input_id": "input-budget",
            "question": "Which market?",
            "expected_input_schema": {"type": "string"},
            "transcript_snapshot": [],
            "expires_at": "2999-01-01T00:00:00+00:00",
        }
    )
    await make_reply_agent_pending_input_handler(db)(
        {
            "turn_id": "turn-budget",
            "pending_input_id": "input-budget",
            "conversation_id": "conversation-1",
            "cas_version": 1,
            "value": "USD",
        }
    )
    turn = (await make_get_agent_turn_handler(db)({"turn_id": "turn-budget"}))["turn"]
    assert turn["tool_rounds"] == 2
    assert turn["terminal_outcome"] is None
    assert turn["obligations"][0]["status"] == "open"
    db.close()

    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.turn_store = AsyncMock()
    orchestrator.turn_store.get_agent_turn.return_value = {
        "tool_rounds": 8,
        "active_time_ms": 0,
        "binds": [],
        "invocations": [],
    }
    orchestrator.tool_executor = MagicMock()
    orchestrator._require_structured_capabilities = AsyncMock()
    expected_failure = MagicMock()
    orchestrator._commit_structured_failure = AsyncMock(return_value=expected_failure)
    obligation = ToolObligation(
        run_id="turn-budget",
        obligation_id="obligation-web",
        required_tool="web_search",
        source="manual",
        evidence_key="",
        argument_predicate=None,
        status=ObligationStatus.OPEN,
    )
    preparation = TurnPreparation(
        filtered_message="Find BTC",
        input_filtered=False,
        request_started_at=__import__("time").perf_counter(),
        call_usages=[],
        prepared_context=PreparedContext(
            entries=[],
            retrieved_memories=[],
            retrieved_messages=None,
            memory_guidance=None,
        ),
        user_message_id=None,
        messages=[{"role": "user", "content": "Find BTC"}],
        openai_tools=[],
        question_route=route_memory_question(""),
        requirements=ResponseRequirements(required_tools=("web_search",)),
        satisfaction=RequirementSatisfaction(),
        run_id="turn-budget",
        finalization_id="final-budget",
        obligations=(obligation,),
    )
    loop = AgenticLoop(injection_flag=False)
    loop.start()
    llm_call = AsyncMock()
    result = await orchestrator._run_structured_authority_turn(
        prepared_turn=preparation,
        session_id="session-1",
        conversation_id="conversation-1",
        loop=loop,
        llm_call=llm_call,
        channel="web",
    )
    assert result is expected_failure
    llm_call.assert_not_awaited()
    assert (
        orchestrator._commit_structured_failure.await_args.kwargs["code"]
        == "evidence_budget_exhausted"
    )
