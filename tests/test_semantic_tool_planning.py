"""Focused contract tests for the structured Stage 1 native protocol."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.agent_loop import AgentOrchestrator, _semantic_tool_choice
from backend.agent.agent_loop_models import PreparedContext, TurnPreparation
from backend.agent.concurrency import AgentConcurrency
from backend.agent.finalization import RequirementSatisfaction, ResponseRequirements
from backend.agent.loop import AgenticLoop
from backend.agent.prompt_builder import PromptBuilder
from backend.agent.semantic_planning import (
    ASK_USER_TOOL_NAME,
    SemanticDecisionKind,
    SemanticProtocolError,
    parse_semantic_decision,
)
from backend.agent.turn_state import ObligationStatus, ToolObligation
from backend.llm.call_factory import (
    LLMResponse,
    LLMToolCall,
    current_thinking_enabled,
    set_thinking_enabled,
)
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.structured_response import StructuredAssistantContent
from backend.memory.classification.question_router import route_memory_question
from backend.tools.broker import ToolBroker
from backend.tools.builtin import HttpFetchInput, WebSearchInput
from backend.tools.registry import (
    PermissionMode,
    PermissionPolicy,
    ToolDefinition,
    ToolRegistry,
)


def _response(*calls, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(tool_calls=list(calls), text=text)


def _call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(id=f"call-{name}", name=name, arguments=arguments)


def test_native_control_protocol() -> None:
    """Plain text finishes while ask-user remains an exclusive control."""
    finish = parse_semantic_decision(_response(text="Готово"))
    assert finish.kind is SemanticDecisionKind.FINISH
    assert finish.answer == "Готово"

    missing = parse_semantic_decision(
        _response(
            _call(
                ASK_USER_TOOL_NAME,
                {
                    "question": "Какой город?",
                    "input_type": "string",
                },
            )
        )
    )
    assert missing.kind is SemanticDecisionKind.ASK_USER

    external = parse_semantic_decision(
        _response(
            _call("web_search", {"query": "btc"}),
            _call("http_fetch", {"url": "https://example.test"}),
        )
    )
    assert external.kind is SemanticDecisionKind.EXTERNAL_TOOLS
    assert [item.name for item in external.external_calls] == [
        "web_search",
        "http_fetch",
    ]

    with pytest.raises(SemanticProtocolError, match="no usable"):
        parse_semantic_decision(_response(text=""))
    with pytest.raises(SemanticProtocolError, match="mixed"):
        parse_semantic_decision(
            _response(
                _call(
                    ASK_USER_TOOL_NAME,
                    {"question": "Which market?", "input_type": "string"},
                ),
                _call("web_search", {"query": "btc"}),
            )
        )
    with pytest.raises(SemanticProtocolError, match="ineligible"):
        parse_semantic_decision(
            _response(_call("invented_tool", {})),
            allowed_external_names={"web_search"},
        )


def test_semantic_choice_bounds_optional_tool_collection() -> None:
    """Two successful calls force plain text while one permits a dependency."""
    choice = _semantic_tool_choice(
        target=None,
        canonical={
            "tool_rounds": 2,
            "invocations": [
                {"tool_name": "web_search", "status": "succeeded"},
                {"tool_name": "http_fetch", "status": "succeeded"},
            ],
        },
        max_rounds=8,
    )
    assert choice == "none"
    assert (
        _semantic_tool_choice(
            target=None,
            canonical={
                "tool_rounds": 1,
                "invocations": [{"tool_name": "web_search", "status": "succeeded"}],
            },
            max_rounds=8,
        )
        == "auto"
    )


@pytest.mark.parametrize(
    ("message", "decision_name"),
    [
        ("Find the current BTC price", "web_search"),
        ("Найди текущий курс биткойна", "web_search"),
        ("Encuentra el precio actual de bitcoin", "web_search"),
        ("ビットコインの現在価格を調べて", "web_search"),
    ],
)
def test_multilingual_selection_has_no_marker_authority(
    message: str,
    decision_name: str,
) -> None:
    """Semantic mode exposes the same catalog regardless of user language."""
    schemas = [
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "http_fetch"}},
    ]
    broker = MagicMock()
    broker.registry.tool_names = ["web_search", "http_fetch"]
    broker.list_openai_schemas.side_effect = lambda *, allowed_names: [
        item for item in schemas if item["function"]["name"] in allowed_names
    ]
    builder = PromptBuilder("test", {}, broker, None)
    assert builder.select_openai_tools(message, semantic_selection=True) == schemas
    selected = parse_semantic_decision(
        _response(_call(decision_name, {"query": message})),
        allowed_external_names={"web_search", "http_fetch"},
    )
    assert selected.external_calls[0].name == "web_search"
    decision = parse_semantic_decision(
        _response(_call("http_fetch", {"url": "https://api.example.test/btc"})),
        allowed_external_names={"web_search", "http_fetch"},
    )
    assert decision.external_calls[0].arguments == {
        "url": "https://api.example.test/btc"
    }


async def test_adaptive_chain_and_thinking(monkeypatch) -> None:
    """The production loop persists and executes dependent search/fetch rounds."""

    class TurnStore:
        def __init__(self) -> None:
            self.state = {
                "turn_id": "turn-1",
                "finalization_id": "final-1",
                "active_time_ms": 0,
                "tool_rounds": 0,
                "enforcement_reasks": 0,
                "synthesis_attempts": 0,
                "obligations": [],
                "binds": [],
                "invocations": [],
            }

        async def get_agent_turn(self, _turn_id):
            return self.state

        async def append_agent_turn_event(self, **kwargs):
            if kwargs["event_type"] == "tool_round_started":
                self.state["tool_rounds"] += 1
            return {}

        async def upsert_agent_tool_batch(self, **kwargs):
            for item in kwargs["invocations"]:
                self.state["invocations"].append(
                    {
                        **item,
                        "status": "required",
                        "batch_id": kwargs["batch_id"],
                    }
                )
            return {}

        async def claim_agent_invocation(self, **kwargs):
            item = next(
                value
                for value in self.state["invocations"]
                if value["invocation_id"] == kwargs["invocation_id"]
            )
            item["status"] = "executing"
            return {"claimed": True, "claim_generation": 1}

        async def complete_agent_invocation(self, **kwargs):
            item = next(
                value
                for value in self.state["invocations"]
                if value["invocation_id"] == kwargs["invocation_id"]
            )
            item["status"] = kwargs["status"]
            item["result"] = kwargs.get("result")
            return {"bound": []}

        async def lookup_capability_grant(self, **_kwargs):
            return None

    store = TurnStore()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "web_search",
            "Search",
            WebSearchInput,
            PermissionPolicy(mode=PermissionMode.IMPLICIT),
        )
    )
    registry.register(
        ToolDefinition(
            "http_fetch",
            "Fetch",
            HttpFetchInput,
            PermissionPolicy(mode=PermissionMode.IMPLICIT),
        )
    )
    executed_names: list[str] = []
    fetch_prices = iter((64000, 65000))

    async def search_handler(_input):
        executed_names.append("web_search")
        return {"url": "https://1.1.1.1/btc"}

    async def fetch_handler(_input):
        executed_names.append("http_fetch")
        return {"price": next(fetch_prices)}

    broker = ToolBroker(registry=registry)
    broker.bind_handler("web_search", search_handler)
    broker.bind_handler("http_fetch", fetch_handler)
    broker.finalize()
    orchestrator = AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda *_args, **_kwargs: [],
        turn_store=store,
    )
    orchestrator.tool_executor.allowed_tool_names = {"web_search", "http_fetch"}
    orchestrator.tool_executor.current_context = {
        "conversation_id": "conversation-1",
        "session_id": "session-1",
        "channel": "scheduler",
        "agent_run_id": "turn-1",
        "tool_policy_version": "registry-v1",
    }
    orchestrator._capabilities_resolver = lambda: SimpleNamespace(
        native_tools=SimpleNamespace(value="supported"),
        required_native_decision=SimpleNamespace(value="supported"),
        non_streaming_custom_schema=SimpleNamespace(value="supported"),
        max_context_tokens=32768,
    )
    orchestrator._require_structured_capabilities = AsyncMock(
        return_value=SimpleNamespace(max_context_tokens=32768)
    )
    expected = MagicMock()
    orchestrator._commit_structured_final = AsyncMock(return_value=expected)

    class Finalizer:
        def __init__(self, **_kwargs):
            pass

        async def finalize(self, _messages, *, call_usages):
            assert call_usages == []
            return SimpleNamespace(
                content=StructuredAssistantContent(answer="BTC is 64000"),
                output_filtered=False,
            )

    monkeypatch.setattr(
        "backend.agent.agent_loop_confirmation.StructuredFinalizer", Finalizer
    )
    responses = iter(
        [
            LLMResponse(
                text="",
                tool_calls=[LLMToolCall("search", "web_search", {"query": "btc"})],
            ),
            LLMResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        "fetch",
                        "http_fetch",
                        {"url": "https://1.1.1.1/btc"},
                    )
                ],
            ),
            LLMResponse(
                text="Use verified evidence",
            ),
        ]
    )
    captured = []
    inherited_thinking = []

    async def llm_call(messages, *, options):
        captured.append((list(messages), options))
        inherited_thinking.append(current_thinking_enabled())
        return next(responses)

    preparation = TurnPreparation(
        filtered_message="Obtain the current BTC price",
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
        messages=[
            {"role": "system", "content": "Use evidence."},
            {"role": "user", "content": "Obtain the current BTC price"},
        ],
        openai_tools=[
            {"type": "function", "function": {"name": "web_search"}},
            {"type": "function", "function": {"name": "http_fetch"}},
            {"type": "function", "function": {"name": ASK_USER_TOOL_NAME}},
        ],
        question_route=route_memory_question(""),
        requirements=ResponseRequirements(),
        satisfaction=RequirementSatisfaction(),
        run_id="turn-1",
        finalization_id="final-1",
    )
    loop = AgenticLoop(injection_flag=False)
    loop.start()
    set_thinking_enabled(True)
    try:
        result = await orchestrator._run_structured_authority_turn(
            prepared_turn=preparation,
            session_id="session-1",
            conversation_id="conversation-1",
            loop=loop,
            llm_call=llm_call,
            channel="scheduler",
        )
    finally:
        set_thinking_enabled(None)
    assert result is expected
    assert executed_names == ["web_search", "http_fetch"]
    assert [item["status"] for item in store.state["invocations"]] == [
        "succeeded",
        "succeeded",
    ]
    assert store.state["tool_rounds"] == 3
    assert "1.1.1.1" in str(captured[1][0])
    assert "64000" in str(captured[2][0])
    assert inherited_thinking == [True, True, True]
    assert all(options.thinking_enabled is None for _, options in captured)
    assert all(
        options.response_format is LlmResponseFormat.TEXT for _, options in captured
    )

    # A URL already present in context must go straight to fetch without search.
    store.state.update(
        tool_rounds=0,
        enforcement_reasks=0,
        synthesis_attempts=0,
        invocations=[],
    )
    executed_names.clear()
    direct_responses = iter(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        "direct-fetch",
                        "http_fetch",
                        {"url": "https://1.1.1.1/btc"},
                    )
                ],
            ),
            LLMResponse(
                text="Use fetched evidence",
            ),
        ]
    )

    async def direct_llm_call(_messages, *, options):
        assert options.response_format is LlmResponseFormat.TEXT
        return next(direct_responses)

    direct_preparation = TurnPreparation(
        filtered_message="Fetch the price from the known endpoint",
        input_filtered=False,
        request_started_at=__import__("time").perf_counter(),
        call_usages=[],
        prepared_context=preparation.prepared_context,
        user_message_id=None,
        messages=[
            {
                "role": "system",
                "content": "Known endpoint: https://1.1.1.1/btc",
            },
            {
                "role": "user",
                "content": "Fetch the price from the known endpoint",
            },
        ],
        openai_tools=preparation.openai_tools,
        question_route=route_memory_question(""),
        requirements=ResponseRequirements(),
        satisfaction=RequirementSatisfaction(),
        run_id="turn-1",
        finalization_id="final-1",
    )
    direct_loop = AgenticLoop(injection_flag=False)
    direct_loop.start()
    direct_result = await orchestrator._run_structured_authority_turn(
        prepared_turn=direct_preparation,
        session_id="session-1",
        conversation_id="conversation-1",
        loop=direct_loop,
        llm_call=direct_llm_call,
        channel="scheduler",
    )
    assert direct_result is expected
    assert executed_names == ["http_fetch"]
    assert store.state["tool_rounds"] == 2


async def test_finish_guarantee_boundary() -> None:
    """No-obligation finish is judgment; an open obligation rejects and fails."""
    finish = parse_semantic_decision(_response(text="No tool is needed."))
    tool = parse_semantic_decision(
        _response(_call("web_search", {"query": "current price"})),
        allowed_external_names={"web_search"},
    )
    assert finish.kind is SemanticDecisionKind.FINISH
    assert finish.external_calls == ()
    assert tool.kind is SemanticDecisionKind.EXTERNAL_TOOLS
    assert tool.external_calls[0].name == "web_search"

    state = {
        "active_time_ms": 0,
        "tool_rounds": 0,
        "enforcement_reasks": 0,
        "obligations": [],
        "binds": [],
        "invocations": [],
    }
    events = []
    store = SimpleNamespace()
    store.get_agent_turn = AsyncMock(side_effect=lambda _turn_id: state)

    async def append_event(**kwargs):
        events.append(kwargs["event_type"])
        if kwargs["event_type"] == "tool_round_started":
            state["tool_rounds"] += 1
        if kwargs["event_type"] == "finish_rejected":
            state["enforcement_reasks"] += 1
        return {}

    store.append_agent_turn_event = append_event
    broker = MagicMock()
    broker.registry.tool_names = ["web_search"]
    orchestrator = AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda *_args, **_kwargs: [],
        turn_store=store,
    )
    orchestrator._require_structured_capabilities = AsyncMock()
    expected_failure = MagicMock()
    orchestrator._commit_structured_failure = AsyncMock(return_value=expected_failure)
    obligation = ToolObligation(
        run_id="turn-1",
        obligation_id="obligation-web",
        required_tool="web_search",
        source="manual",
        evidence_key="",
        argument_predicate=None,
        status=ObligationStatus.OPEN,
    )
    preparation = TurnPreparation(
        filtered_message="Find current price",
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
        messages=[{"role": "user", "content": "Find current price"}],
        openai_tools=[
            {"type": "function", "function": {"name": "web_search"}},
            {"type": "function", "function": {"name": ASK_USER_TOOL_NAME}},
        ],
        question_route=route_memory_question(""),
        requirements=ResponseRequirements(required_tools=("web_search",)),
        satisfaction=RequirementSatisfaction(),
        run_id="turn-1",
        finalization_id="final-1",
        obligations=(obligation,),
    )
    llm_call = AsyncMock(
        side_effect=[
            LLMResponse(
                text="Skip search",
            ),
            LLMResponse(
                text="Still skip search",
            ),
        ]
    )
    loop = AgenticLoop(injection_flag=False)
    loop.start()
    result = await orchestrator._run_structured_authority_turn(
        prepared_turn=preparation,
        session_id="session-1",
        conversation_id="conversation-1",
        loop=loop,
        llm_call=llm_call,
        channel="web",
    )
    assert result is expected_failure
    assert "finish_rejected" in events
    assert (
        orchestrator._commit_structured_failure.await_args.kwargs["code"]
        == "finish_with_open_obligation"
    )
