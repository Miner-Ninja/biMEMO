"""Tests for gateway orchestrator.

Covers zone splitting, tool result message building,
parallel tool execution via asyncio.gather, and the
full agentic loop with native function calling.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.agent_loop_helpers import (
    build_dialog_step_record_input,
    build_tool_result_messages,
    message_allows_file_read_tools,
    message_allows_file_write_tool,
    request_non_empty_final_answer,
)
from backend.agent.agent_loop_models import (
    AgentResponse,
    ToolCall,
)
from backend.agent.concurrency import (
    AgentConcurrency,
)
from backend.agent.finalization import (
    RequirementSatisfaction,
    ResponseRequirements,
    build_required_tool_failure_answer,
    looks_like_status_only_answer,
    validate_final_answer,
)
from backend.agent.llm_caller import LLMCaller
from backend.agent.loop import LoopState
from backend.llm.call_factory import AttemptRecord, LLMResponse, LLMToolCall
from backend.llm.capabilities import CapabilitySupport, LlmResponseFormat
from backend.llm.structured_response import StructuredAssistantContent
from backend.llm.usage import LLMCallUsage
from backend.memory.context_builder import ContextEntry, ContextResult
from backend.memory.evidence_chain_models import EvidenceBundle
from backend.permissions import ActionDecision, DecisionKind
from backend.security.content_scanning_models import FilterResult
from backend.tools.broker import ToolResult
from backend.trust.confirmation import PendingAction


def _text_response(text: str) -> LLMResponse:
    """Create an LLMResponse with plain text (no tool calls)."""
    return LLMResponse(
        text=text,
        tool_calls=[],
        raw_message={"role": "assistant", "content": text},
    )


def _tool_response(
    tool_calls: list[tuple[str, str, dict]],
    text: str = "",
) -> LLMResponse:
    """Create an LLMResponse with tool calls.

    Args:
        tool_calls: List of (id, name, arguments) tuples.
        text: Optional text alongside tool calls.
    """
    tc_objects = [
        LLMToolCall(id=tc_id, name=name, arguments=args)
        for tc_id, name, args in tool_calls
    ]
    raw_message: dict = {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            }
            for tc_id, name, args in tool_calls
        ],
    }
    return LLMResponse(
        text=text,
        tool_calls=tc_objects,
        raw_message=raw_message,
    )


class TestBuildToolResultMessages:
    """Tests for tool result message building."""

    def test_success_result(self):
        """Verify success result produces role:tool message."""
        calls = [ToolCall(id="call_1", name="file_read", parameters={})]
        results = [
            ToolResult(
                tool_name="file_read",
                success=True,
                result="content",
                duration_ms=1.0,
            )
        ]
        msgs = build_tool_result_messages(calls, results)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "call_1"
        assert json.loads(msgs[0]["content"]) == {
            "data": "content",
            "invocation_id": "call_1",
            "original_bytes": 7,
            "source": "file_read",
            "truncated": False,
        }

    def test_error_result(self):
        """Verify error result produces role:tool message."""
        calls = [ToolCall(id="call_2", name="file_read", parameters={})]
        results = [
            ToolResult(
                tool_name="file_read",
                success=False,
                result="not found",
                duration_ms=1.0,
            )
        ]
        msgs = build_tool_result_messages(calls, results)
        assert json.loads(msgs[0]["content"]) == {
            "data": "not found",
            "invocation_id": "call_2",
            "original_bytes": 9,
            "source": "file_read",
            "truncated": False,
        }

    def test_multiple_results(self):
        """Verify multiple results produce matching messages."""
        calls = [
            ToolCall(id="call_a", name="a", parameters={}),
            ToolCall(id="call_b", name="b", parameters={}),
        ]
        results = [
            ToolResult(
                tool_name="a",
                success=True,
                result="r1",
                duration_ms=1.0,
            ),
            ToolResult(
                tool_name="b",
                success=True,
                result="r2",
                duration_ms=1.0,
            ),
        ]
        msgs = build_tool_result_messages(calls, results)
        assert len(msgs) == 2
        assert msgs[0]["tool_call_id"] == "call_a"
        assert msgs[1]["tool_call_id"] == "call_b"


class TestFinalAnswerRecovery:
    """Tests for one extra final-answer request after tool execution."""

    def test_placeholder_final_answer_detection(self):
        """Treat short status phrases as placeholders, not final answers."""
        assert looks_like_status_only_answer(
            "Ищу инструкции по настройке MPT для ускорения генерации."
        )
        assert looks_like_status_only_answer("Looking up the latest information.")
        assert looks_like_status_only_answer(
            "I'll search for the current prices of Bitcoin."
        )
        assert looks_like_status_only_answer(
            "I need to search for the current crypto prices first."
        )
        assert not looks_like_status_only_answer(
            "Я нашел инструкции и вот краткая выжимка: настройте параметр X."
        )

    def test_finalization_rejects_unsatisfied_current_data(self):
        """Do not accept answers when mandatory evidence is missing."""
        decision = validate_final_answer(
            raw_final_text="Bitcoin is trading near 100.",
            structured_content=None,
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=RequirementSatisfaction(),
        )

        assert not decision.accepted
        assert decision.requires_recovery

    def test_finalization_accepts_satisfied_current_data(self):
        """Accept non-placeholder answers after mandatory evidence exists."""
        decision = validate_final_answer(
            raw_final_text="Bitcoin is trading near 100.",
            structured_content=None,
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=RequirementSatisfaction(
                satisfied_tools=frozenset({"web_search"}),
            ),
        )

        assert decision.accepted

    def test_finalization_rejects_missing_current_data_target_without_notice(self):
        """Missing target evidence must be explicit in current-data answers."""
        decision = validate_final_answer(
            raw_final_text="Bitcoin is 100. Ethereum is 200.",
            structured_content=None,
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=RequirementSatisfaction(
                satisfied_tools=frozenset({"web_search"}),
                current_data_targets=("Bitcoin", "Ethereum", "Solana"),
                missing_current_data_targets=("Solana",),
            ),
        )

        assert not decision.accepted
        assert decision.requires_recovery

    def test_finalization_accepts_missing_current_data_target_with_notice(self):
        """An answer can finish when it clearly reports missing evidence."""
        decision = validate_final_answer(
            raw_final_text=(
                "Bitcoin is 100. Ethereum is 200. Solana data was not found "
                "in the available sources."
            ),
            structured_content=None,
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=RequirementSatisfaction(
                satisfied_tools=frozenset({"web_search"}),
                current_data_targets=("Bitcoin", "Ethereum", "Solana"),
                missing_current_data_targets=("Solana",),
            ),
        )

        assert decision.accepted

    def test_with_current_data_targets_covered_drops_covered(self):
        """Covered targets leave the missing set; others stay (R1)."""
        satisfaction = RequirementSatisfaction(
            current_data_targets=("Bitcoin", "Ethereum", "Solana"),
            missing_current_data_targets=("Ethereum", "Solana"),
        )

        updated = satisfaction.with_current_data_targets_covered(("ethereum",))

        assert updated.missing_current_data_targets == ("Solana",)
        assert updated.current_data_targets == ("Bitcoin", "Ethereum", "Solana")

    def test_finalization_accepts_when_target_covered_by_fetch(self):
        """A fetch covering the target clears the guard's rejection (R1, R3)."""
        satisfaction = RequirementSatisfaction(
            satisfied_tools=frozenset({"web_search"}),
            current_data_targets=("Ethereum",),
            missing_current_data_targets=("Ethereum",),
        ).with_current_data_targets_covered(("Ethereum",))

        decision = validate_final_answer(
            raw_final_text="Ethereum is 1902.5 USD.",
            structured_content=None,
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=satisfaction,
        )

        assert decision.accepted

    def test_required_tool_failure_answer_is_clear(self):
        """Return a deterministic answer when mandatory evidence failed."""
        answer = build_required_tool_failure_answer(
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=RequirementSatisfaction(
                failed_tools={"web_search": "timeout"},
            ),
        )

        assert "could not fetch current data" in answer
        assert "timeout" in answer

    @pytest.mark.asyncio
    async def test_run_model_loop_returns_failure_when_required_web_search_failed(
        self,
    ):
        """Do not call the LLM when mandatory evidence already failed."""
        llm_caller = LLMCaller(
            model_name="qwen3.5:9b",
            broker=MagicMock(),
        )
        loop = MagicMock()
        tool_executor = MagicMock()
        llm_call = AsyncMock()

        result = await llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=[{"role": "user", "content": "current BTC price?"}],
            openai_tools=[],
            call_usages=[],
            channel="chat",
            tool_executor=tool_executor,
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=RequirementSatisfaction(
                failed_tools={"web_search": "timeout"},
            ),
        )

        assert "could not fetch current data" in result.raw_final_text
        llm_call.assert_not_awaited()
        tool_executor.run_one_tool_round.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_model_loop_buffers_tokens_for_required_tool_turns(self):
        """Buffer answer tokens while still streaming model reasoning."""
        llm_caller = LLMCaller(
            model_name="qwen3.5:9b",
            broker=MagicMock(),
        )
        llm_caller.sse_queue = asyncio.Queue()
        loop = MagicMock()
        loop.check_limits.return_value = LoopState.RUNNING
        tool_executor = MagicMock()
        tool_executor.run_one_tool_round = AsyncMock(
            return_value=MagicMock(
                raw_final_text="BTC is 100.",
                structured_content=None,
                executed_immediate_tools=False,
                should_continue=False,
            )
        )
        captured_kwargs = {}

        async def llm_call(messages, **kwargs):
            """Capture LLM call kwargs and return one text response."""
            del messages
            captured_kwargs.update(kwargs)
            stream_queue = kwargs.get("sse_queue_override")
            await stream_queue.put({"type": "token", "content": "draft"})
            await stream_queue.put({"type": "thinking", "content": "checking"})
            return _text_response("BTC is 100.")

        result = await llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=[{"role": "user", "content": "current BTC price?"}],
            openai_tools=[],
            call_usages=[],
            channel="chat",
            tool_executor=tool_executor,
            requirements=ResponseRequirements(
                needs_current_data=True,
                required_tools=("web_search",),
            ),
            satisfaction=RequirementSatisfaction(
                satisfied_tools=frozenset({"web_search"}),
            ),
        )

        assert result.raw_final_text == "BTC is 100."
        assert captured_kwargs["sse_queue_override"] is not llm_caller.sse_queue
        assert await llm_caller.sse_queue.get() == {
            "type": "thinking",
            "content": "checking",
        }
        assert llm_caller.sse_queue.empty()

    @pytest.mark.asyncio
    async def test_request_non_empty_final_answer_keeps_structured_sections(self):
        """Keep the ready sections when the recovery call already returned them."""
        llm_call = AsyncMock(
            return_value=LLMResponse(
                text="Visible answer",
                raw_message={"role": "assistant", "content": "Visible answer"},
                structured_content=StructuredAssistantContent(
                    answer="Visible answer",
                    suggestions=["Next step"],
                ),
            )
        )
        usages: list[LLMCallUsage] = []

        recovered = await request_non_empty_final_answer(
            llm_call=llm_call,
            messages=[{"role": "user", "content": "Hi"}],
            call_usages=usages,
        )

        assert recovered == StructuredAssistantContent(
            answer="Visible answer",
            suggestions=["Next step"],
        )

    @pytest.mark.asyncio
    async def test_run_model_loop_keeps_recovered_suggestions_after_tools(self):
        """Keep suggestions when one final recovery call is needed."""
        llm_caller = LLMCaller(
            model_name="qwen3.5:9b",
            broker=MagicMock(),
        )
        loop = MagicMock()
        tool_executor = MagicMock()
        tool_executor.run_one_tool_round = AsyncMock(
            side_effect=[
                MagicMock(
                    raw_final_text="",
                    structured_content=None,
                    executed_immediate_tools=True,
                    should_continue=True,
                ),
                MagicMock(
                    raw_final_text="",
                    structured_content=None,
                    executed_immediate_tools=False,
                    should_continue=False,
                ),
            ]
        )
        llm_call = AsyncMock(
            side_effect=[
                _tool_response([("call-1", "web_search", {"query": "eth"})]),
                _text_response(""),
                LLMResponse(
                    text="Visible answer",
                    raw_message={"role": "assistant", "content": "Visible answer"},
                    structured_content=StructuredAssistantContent(
                        answer="Visible answer",
                        suggestions=["Next step"],
                    ),
                ),
            ]
        )
        call_usages: list[LLMCallUsage] = []

        loop.check_limits.side_effect = [LoopState.RUNNING, LoopState.RUNNING]

        result = await llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=[{"role": "user", "content": "Hi"}],
            openai_tools=[],
            call_usages=call_usages,
            channel="chat",
            tool_executor=tool_executor,
        )

        assert result.raw_final_text == "Visible answer"
        assert result.structured_content == StructuredAssistantContent(
            answer="Visible answer",
            suggestions=["Next step"],
        )

    @pytest.mark.asyncio
    async def test_run_model_loop_recovers_placeholder_after_tools(self):
        """Recover when the model returns a searching status after tool results."""
        llm_caller = LLMCaller(
            model_name="qwen3.5:9b",
            broker=MagicMock(),
        )
        loop = MagicMock()
        tool_executor = MagicMock()
        tool_executor.run_one_tool_round = AsyncMock(
            side_effect=[
                MagicMock(
                    raw_final_text="",
                    structured_content=None,
                    executed_immediate_tools=True,
                    should_continue=True,
                ),
                MagicMock(
                    raw_final_text="Ищу инструкции по настройке MPT.",
                    structured_content=StructuredAssistantContent(
                        answer="Ищу инструкции по настройке MPT.",
                        suggestions=[],
                    ),
                    executed_immediate_tools=False,
                    should_continue=False,
                ),
            ]
        )
        llm_call = AsyncMock(
            side_effect=[
                _tool_response([("call-1", "web_search", {"query": "qwen mpt"})]),
                LLMResponse(
                    text="Ищу инструкции по настройке MPT.",
                    raw_message={
                        "role": "assistant",
                        "content": "Ищу инструкции по настройке MPT.",
                    },
                    structured_content=StructuredAssistantContent(
                        answer="Ищу инструкции по настройке MPT.",
                        suggestions=[],
                    ),
                ),
                LLMResponse(
                    text="Настройка MPT выполняется через параметры сервера.",
                    raw_message={
                        "role": "assistant",
                        "content": "Настройка MPT выполняется через параметры сервера.",
                    },
                    structured_content=StructuredAssistantContent(
                        answer="Настройка MPT выполняется через параметры сервера.",
                        suggestions=[
                            "Show an MPT configuration example",
                            "Compare speed with and without MPT",
                            "Find Qwen MPT documentation",
                        ],
                    ),
                ),
            ]
        )
        call_usages: list[LLMCallUsage] = []
        loop.check_limits.side_effect = [LoopState.RUNNING, LoopState.RUNNING]

        result = await llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=[{"role": "user", "content": "Найди инструкции по MPT"}],
            openai_tools=[],
            call_usages=call_usages,
            channel="chat",
            tool_executor=tool_executor,
        )

        assert (
            result.raw_final_text
            == "Настройка MPT выполняется через параметры сервера."
        )
        assert result.structured_content == StructuredAssistantContent(
            answer="Настройка MPT выполняется через параметры сервера.",
            suggestions=[
                "Show an MPT configuration example",
                "Compare speed with and without MPT",
                "Find Qwen MPT documentation",
            ],
        )

    @pytest.mark.asyncio
    async def test_run_model_loop_recovers_placeholder_without_tools(self):
        """Recover when the model returns a status phrase without tool calls."""
        llm_caller = LLMCaller(
            model_name="qwen3.5:9b",
            broker=MagicMock(),
        )
        loop = MagicMock()
        loop.check_limits.return_value = LoopState.RUNNING
        tool_executor = MagicMock()
        tool_executor.run_one_tool_round = AsyncMock(
            return_value=MagicMock(
                raw_final_text="I'll search for the current prices of Bitcoin.",
                structured_content=StructuredAssistantContent(
                    answer="I'll search for the current prices of Bitcoin.",
                    suggestions=[],
                ),
                executed_immediate_tools=False,
                should_continue=False,
            )
        )
        llm_call = AsyncMock(
            side_effect=[
                LLMResponse(
                    text="I'll search for the current prices of Bitcoin.",
                    raw_message={
                        "role": "assistant",
                        "content": "I'll search for the current prices of Bitcoin.",
                    },
                    structured_content=StructuredAssistantContent(
                        answer="I'll search for the current prices of Bitcoin.",
                        suggestions=[],
                    ),
                ),
                LLMResponse(
                    text="Bitcoin is trading near 100.",
                    raw_message={
                        "role": "assistant",
                        "content": "Bitcoin is trading near 100.",
                    },
                    structured_content=StructuredAssistantContent(
                        answer="Bitcoin is trading near 100.",
                        suggestions=["Check ETH", "Check SOL", "Compare all three"],
                    ),
                ),
            ]
        )

        result = await llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=[{"role": "user", "content": "Current BTC price?"}],
            openai_tools=[],
            call_usages=[],
            channel="chat",
            tool_executor=tool_executor,
        )

        assert result.raw_final_text == "Bitcoin is trading near 100."
        assert result.structured_content == StructuredAssistantContent(
            answer="Bitcoin is trading near 100.",
            suggestions=["Check ETH", "Check SOL", "Compare all three"],
        )

    @pytest.mark.asyncio
    async def test_run_model_loop_recovers_missing_suggestions(self):
        """Ask the model for suggestions when the final answer omitted them."""
        llm_caller = LLMCaller(
            model_name="qwen3.5:9b",
            broker=MagicMock(),
        )
        loop = MagicMock()
        loop.check_limits.return_value = LoopState.RUNNING
        tool_executor = MagicMock()
        tool_executor.run_one_tool_round = AsyncMock(
            return_value=MagicMock(
                raw_final_text="Visible answer",
                structured_content=StructuredAssistantContent(
                    answer="Visible answer",
                    suggestions=[],
                ),
                executed_immediate_tools=False,
                should_continue=False,
            )
        )
        llm_call = AsyncMock(
            side_effect=[
                LLMResponse(
                    text="Visible answer",
                    raw_message={"role": "assistant", "content": "Visible answer"},
                    structured_content=StructuredAssistantContent(
                        answer="Visible answer",
                        suggestions=[],
                    ),
                ),
                _text_response(
                    '{"suggestions":["Check current GRAM news",'
                    '"Compare GRAM and TON","Find the official GRAM website"]}'
                ),
            ]
        )
        call_usages: list[LLMCallUsage] = []

        result = await llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=[{"role": "user", "content": "How is GRAM related to Durov?"}],
            openai_tools=[],
            call_usages=call_usages,
            channel="chat",
            tool_executor=tool_executor,
        )

        assert result.raw_final_text == "Visible answer"
        assert result.structured_content == StructuredAssistantContent(
            answer="Visible answer",
            suggestions=[
                "Check current GRAM news",
                "Compare GRAM and TON",
                "Find the official GRAM website",
            ],
        )
        assert llm_call.await_count == 2

    @pytest.mark.asyncio
    async def test_run_model_loop_does_not_recover_complete_suggestions(self):
        """Do not spend another LLM call when three suggestions already exist."""
        complete_suggestions = ["First step", "Second step", "Third step"]
        llm_caller = LLMCaller(
            model_name="qwen3.5:9b",
            broker=MagicMock(),
        )
        loop = MagicMock()
        loop.check_limits.return_value = LoopState.RUNNING
        tool_executor = MagicMock()
        tool_executor.run_one_tool_round = AsyncMock(
            return_value=MagicMock(
                raw_final_text="Visible answer",
                structured_content=StructuredAssistantContent(
                    answer="Visible answer",
                    suggestions=complete_suggestions,
                ),
                executed_immediate_tools=False,
                should_continue=False,
            )
        )
        llm_call = AsyncMock(
            return_value=LLMResponse(
                text="Visible answer",
                raw_message={"role": "assistant", "content": "Visible answer"},
                structured_content=StructuredAssistantContent(
                    answer="Visible answer",
                    suggestions=complete_suggestions,
                ),
            )
        )

        result = await llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=[{"role": "user", "content": "Hi"}],
            openai_tools=[],
            call_usages=[],
            channel="chat",
            tool_executor=tool_executor,
        )

        assert result.structured_content == StructuredAssistantContent(
            answer="Visible answer",
            suggestions=complete_suggestions,
        )
        assert llm_call.await_count == 1


def _make_orchestrator(
    broker: object | None = None,
    context: list | None = None,
) -> AgentOrchestrator:
    """Create an orchestrator with test defaults."""
    if broker is None:
        broker = MagicMock()
    ctx = context if context is not None else []
    orchestrator = AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda sid, **kwargs: ctx,
    )
    if not hasattr(broker, "finalized") or isinstance(broker, MagicMock):
        orchestrator.tool_executor.action_decisions.decide = MagicMock(
            side_effect=lambda context: ActionDecision(
                DecisionKind.ASK_ONCE,
                details={
                    "permission_mode": "per_invocation",
                    "policy_revision": "registry-v1",
                },
            )
            if context.tool_name == "email_send"
            else ActionDecision(
                DecisionKind.ALLOW,
                details={
                    "permission_mode": "implicit",
                    "policy_revision": "registry-v1",
                },
            )
        )
    return orchestrator


class TestAgentOrchestrator:
    """Tests for the full orchestrator loop."""

    async def test_live_active_deadline_blocks_next_provider_call(self) -> None:
        """Current foreground elapsed time counts before another native round."""
        orchestrator = _make_orchestrator()
        orchestrator.turn_store = SimpleNamespace(
            get_agent_turn=AsyncMock(return_value={"active_time_ms": 0})
        )
        orchestrator._capabilities_resolver = lambda: SimpleNamespace(
            native_tools=CapabilitySupport.SUPPORTED
        )
        expected = MagicMock()
        orchestrator._commit_structured_failure = AsyncMock(return_value=expected)
        llm_call = AsyncMock()
        prepared = SimpleNamespace(
            run_id="turn-1",
            obligations=(),
            request_started_at=time.perf_counter() - 10_000,
        )

        result = await orchestrator._run_structured_authority_turn(
            prepared_turn=prepared,
            session_id="session-1",
            conversation_id="conversation-1",
            loop=MagicMock(),
            llm_call=llm_call,
            channel="web",
        )

        assert result is expected
        llm_call.assert_not_awaited()
        assert (
            orchestrator._commit_structured_failure.await_args.kwargs["code"]
            == "active_deadline_exhausted"
        )

    def test_output_filtered_stays_boolean_when_credentials_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return a boolean flag even when output filtering finds secrets."""
        orchestrator = _make_orchestrator()
        loop = MagicMock()
        loop.complete.return_value = MagicMock()

        monkeypatch.setattr(
            "backend.agent.agent_loop.filter_output",
            lambda _text: FilterResult(
                text="[REDACTED]",
                credentials_found=["Password in text"],
            ),
        )

        finalized_response = orchestrator._finalize_assistant_response(
            raw_final_text="password=secret",
            loop=loop,
            call_usages=[],
            request_started_at=time.perf_counter(),
            structured_content=None,
        )

        assert finalized_response.response_text == "[REDACTED]"
        assert finalized_response.output_filtered is True

    def test_file_write_tool_requires_explicit_file_intent(self):
        """Expose file_write only for clear file-edit requests."""
        assert not message_allows_file_write_tool(
            "Привет! Меня зовут Дмитрий, запомни это."
        )
        assert message_allows_file_write_tool(
            "Create a README.md file and write deployment steps into it."
        )

    def test_file_read_tools_require_explicit_file_intent(self):
        """Do not expose file_read/file_search for pasted code analysis."""
        pasted_code = (
            "Проанализируй этот код:\n```python\ndef hello():\n    return 'world'\n```"
        )
        assert not message_allows_file_read_tools(pasted_code)
        assert message_allows_file_read_tools(
            "Проанализируй файл sandbox/CODE_RULES.md"
        )
        assert message_allows_file_read_tools(
            "Search files in app/backend for file_read"
        )

    async def test_non_english_online_request_has_no_marker_classifier(
        self,
    ):
        """Language-specific text cannot trigger a backend tool decision."""
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)
        llm_call_count = 0

        async def mock_llm(messages):
            del messages
            nonlocal llm_call_count
            llm_call_count += 1
            return _text_response("BTC is trading at 100.")

        resp = await orchestrator.process_message(
            message="Prašau paieškok internete, koks dabar bitkoino kursas",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert "BTC is trading at 100" in resp.structured_content.answer
        assert llm_call_count == 1
        broker.execute.assert_not_awaited()

    async def test_no_marker_classifier_uses_the_normal_stream(self):
        """One semantic model call uses the normal chat stream directly."""
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)
        orchestrator.sse_queue = object()

        llm_call_count = 0

        async def mock_llm(
            messages,
            sse_queue_override=None,
            *,
            tools_override=None,
        ):
            del messages
            del tools_override
            nonlocal llm_call_count
            llm_call_count += 1
            assert sse_queue_override is orchestrator.sse_queue
            return _text_response("BTC is trading at 100.")

        resp = await orchestrator.process_message(
            message="Prašau paieškok internete, koks dabar bitkoino kursas",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert "BTC is trading at 100" in resp.structured_content.answer
        assert llm_call_count == 1
        broker.execute.assert_not_awaited()

    async def test_regular_question_skips_forced_web_search_classifier(self):
        """Do not add an extra classifier call for an ordinary question."""
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)

        llm_call_count = 0

        async def mock_llm(messages):
            nonlocal llm_call_count
            llm_call_count += 1
            return _text_response("Bitcoin is a decentralized digital asset.")

        resp = await orchestrator.process_message(
            message="Объясни, что такое биткойн",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )

        assert "Bitcoin" in resp.structured_content.answer
        assert llm_call_count == 1
        broker.execute.assert_not_called()

    async def test_simple_response(self):
        """Verify simple message with no tool calls."""
        orchestrator = _make_orchestrator()

        async def mock_llm(messages):
            return _text_response("Hello! How can I help?")

        resp = await orchestrator.process_message(
            message="hi",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert isinstance(resp, AgentResponse)
        assert "Hello" in resp.structured_content.answer

    async def test_structured_direct_answer_always_uses_json_finalization(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even an ordinary chat is normalized through the final JSON schema."""
        from backend.config import reset_settings

        reset_settings()
        broker = MagicMock()
        broker.registry.tool_names = []
        broker.list_openai_schemas.return_value = []
        turn_store = AsyncMock()
        turn_store.get_agent_turn.return_value = {
            "finalization_id": "final-1",
            "synthesis_attempts": 0,
        }
        turn_store.get_pending_agent_clarification.return_value = None
        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=lambda _session_id, **_kwargs: [],
            turn_store=turn_store,
        )
        orchestrator._capabilities_resolver = lambda: SimpleNamespace(
            native_tools=CapabilitySupport.SUPPORTED,
            required_native_decision=CapabilitySupport.SUPPORTED,
            non_streaming_custom_schema=CapabilitySupport.SUPPORTED,
            max_context_tokens=262_144,
        )
        expected = MagicMock()
        orchestrator._commit_structured_final = AsyncMock(return_value=expected)
        calls = 0

        async def mock_llm(_messages, *, options=None, phase_override=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                assert options.response_format is LlmResponseFormat.TEXT
                assert options.thinking_enabled is None
                assert options.tool_choice == "auto"
                assert {
                    item["function"]["name"] for item in options.tools
                } >= {"bimemo_ask_user"}
                return LLMResponse(text="Hello draft")
            assert phase_override == "Stage 2"
            assert options.response_format is LlmResponseFormat.JSON_SCHEMA
            assert options.tools == []
            return LLMResponse(
                text=(
                    '{"answer":"Hello in JSON","suggestions":'
                    '["Ask another question"]}'
                )
            )

        try:
            response = await orchestrator.process_message(
                message="Привет",
                channel="web",
                llm_call=mock_llm,
                session_id="session-1",
                conversation_id="conversation-1",
                agent_run_id="turn-1",
            )
        finally:
            reset_settings()

        assert response is expected
        assert calls == 2
        content = orchestrator._commit_structured_final.await_args.kwargs[
            "structured_content"
        ]
        assert content.answer == "Hello in JSON"
        assert content.suggestions == ["Ask another question"]
        assert any(
            call.kwargs.get("event_type") == "semantic_finish_without_tool"
            for call in turn_store.append_agent_turn_event.await_args_list
        )

    async def test_explicit_online_words_do_not_run_backend_prefetch(self):
        """Tool selection belongs to the model, not a phrase preflight."""
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)

        seen_messages: list[dict[str, object]] = []

        async def mock_llm(messages):
            nonlocal seen_messages
            seen_messages = list(messages)
            return _text_response("BTC is trading at 100.")

        resp = await orchestrator.process_message(
            message="Тогда узнай в интернете какой курс биткойна",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )

        assert "BTC is trading at 100" in resp.structured_content.answer
        broker.execute.assert_not_awaited()
        assert not any(
            "fetched before" in str(message.get("content", "")).lower()
            for message in seen_messages
        )

    async def test_current_data_words_do_not_create_backend_obligation(self):
        """A phrase alone cannot fail the turn before semantic planning."""
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)
        llm_call = AsyncMock(return_value=_text_response("Need semantic planning."))

        resp = await orchestrator.process_message(
            message="какой курс биткойна на данный момент?",
            channel="web",
            llm_call=llm_call,
            conversation_id="test-conv",
        )

        assert resp.structured_content.answer == "Need semantic planning."
        broker.execute.assert_not_awaited()
        llm_call.assert_awaited_once()

    async def test_clear_pending(self):
        """Verify clear_pending empties list."""
        orchestrator = _make_orchestrator()
        orchestrator._pending_red = [
            PendingAction(
                action_id="test-id",
                tool_name="http_fetch",
                parameters={},
                channel="web",
                created_at=0.0,
            )
        ]
        orchestrator.clear_pending()
        assert orchestrator.pending_red_calls == []

    async def test_context_loaded(self):
        """Verify context entries are included in messages."""
        ctx_entries = [
            ContextEntry(role="system", content="You are helpful."),
        ]
        orchestrator = _make_orchestrator(context=ctx_entries)

        async def mock_llm(messages):
            # messages[0] is the orchestrator system prompt
            # messages[1] is the context entry
            assert messages[1]["role"] == "system"
            assert messages[1]["content"] == "You are helpful."
            return _text_response("Hello")

        await orchestrator.process_message(
            message="hi",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

    async def test_memory_query_with_long_term_context_adds_guardrail(self):
        """Verify memory meta-questions get a long-term contradiction guardrail."""
        ctx_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] Active preference for future technical "
                    "and future debugging answers is detailed, step-by-step, with "
                    "context and reasoning."
                ),
            ),
        ]

        def context_fn(session_id: str, **kwargs) -> ContextResult:
            del session_id
            del kwargs
            return ContextResult(
                entries=ctx_entries,
                retrieved_memories=[],
                retrieved_messages=None,
                retrieved_reflections=None,
            )

        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=context_fn,
        )

        async def mock_llm(messages):
            assert any(
                message["role"] == "system"
                and "Memory-answer rule:" in message["content"]
                for message in messages
            )
            return _text_response("ok")

        await orchestrator.process_message(
            message="What is the durable preference now?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

    async def test_context_memory_guidance_is_added_as_system_message(self):
        """Compressed memory guidance should be appended as a system block."""
        ctx_entries = [
            ContextEntry(
                role="summary",
                content="[Retrieved summary] Older work context was compressed.",
            ),
        ]

        def context_fn(session_id: str, **kwargs) -> ContextResult:
            del session_id
            del kwargs
            return ContextResult(
                entries=ctx_entries,
                retrieved_memories=[],
                memory_guidance=(
                    "Compressed memory guidance:\n"
                    "- Treat summaries as compressed memory."
                ),
                retrieved_messages=None,
                retrieved_reflections=None,
            )

        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=context_fn,
        )

        async def mock_llm(messages):
            assert messages[1]["role"] == "system"
            assert "Compressed memory guidance:" in messages[1]["content"]
            return _text_response("ok")

        await orchestrator.process_message(
            message="What do you remember about my work stack?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

    async def test_evidence_recall_question_adds_source_backed_context(self):
        """Explicit source questions should add evidence support to the prompt."""
        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=lambda sid, **kwargs: [],
            evidence_bundle_fn=AsyncMock(
                return_value=EvidenceBundle(
                    summary={"main_point": "User prefers detailed debugging answers."},
                    records=[
                        {"summary": ("The user asked for step-by-step debugging help.")}
                    ],
                    source_messages=[
                        {
                            "role": "user",
                            "content": (
                                "Please explain debugging issues step by step."
                            ),
                        }
                    ],
                )
            ),
        )

        async def mock_llm(messages):
            assert any(
                message["role"] == "system"
                and "Evidence recall support:" in message["content"]
                and "Source messages:" in message["content"]
                for message in messages
            )
            return _text_response("ok")

        await orchestrator.process_message(
            message="Which messages support what you remember about Ollama?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

    async def test_normal_memory_question_does_not_call_evidence_loader(self):
        """Normal recall should stay on the existing memory path."""
        evidence_bundle_fn = AsyncMock()
        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=lambda sid, **kwargs: [],
            evidence_bundle_fn=evidence_bundle_fn,
        )

        async def mock_llm(messages):
            assert not any(
                message["role"] == "system"
                and "Evidence recall support:" in message["content"]
                for message in messages
            )
            return _text_response("ok")

        await orchestrator.process_message(
            message="What do you remember about Ollama?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        evidence_bundle_fn.assert_not_called()

    async def test_persistence_question_uses_structured_mode_without_llm(self):
        """Persistence questions should use the deterministic memory answer mode."""
        ctx_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] The user's preference has shifted from "
                    "brief answers to detailed, step-by-step technical and "
                    "debugging responses with full context and reasoning."
                ),
            ),
            ContextEntry(
                role="record",
                content=(
                    "[Retrieved memory] This validation run is temporary session "
                    "context and should stay separate from the durable preference."
                ),
            ),
        ]

        def context_fn(session_id: str, **kwargs) -> ContextResult:
            del session_id
            del kwargs
            return ContextResult(
                entries=ctx_entries,
                retrieved_memories=[],
                retrieved_messages=None,
                retrieved_reflections=None,
            )

        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=context_fn,
        )

        async def mock_llm(messages):
            raise AssertionError(
                "Persistence questions should not call the free-form LLM path.",
            )

        resp = await orchestrator.process_message(
            message=(
                "If I return tomorrow, what answer style should you still remember?"
            ),
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )
        assert "Long-term preference:" in resp.structured_content.answer
        assert "Detailed, step-by-step answers" in resp.structured_content.answer
        assert "Temporary short-term context:" in resp.structured_content.answer

    async def test_writes_memory_route_diagnostic_after_routing(self):
        """Structured route snapshots should be persisted after classification."""
        written_diagnostics: list[dict[str, object]] = []

        def context_fn(session_id: str, **kwargs) -> ContextResult:
            del session_id
            del kwargs
            return ContextResult(
                entries=[],
                retrieved_memories=[],
                retrieved_messages=None,
                retrieved_reflections=None,
            )

        async def write_memory_route_diagnostic_fn(**payload):
            written_diagnostics.append(payload)

        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=context_fn,
            write_memory_route_diagnostic_fn=write_memory_route_diagnostic_fn,
        )

        async def mock_llm(messages):
            del messages
            return _text_response("normal answer")

        await orchestrator.process_message(
            message="What did I tell you about Ollama?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="c1",
        )

        assert len(written_diagnostics) == 1
        diagnostic_payload = written_diagnostics[0]
        assert diagnostic_payload["session_id"] == "s1"
        assert diagnostic_payload["conversation_id"] == "c1"
        assert diagnostic_payload["channel"] == "web"
        assert diagnostic_payload["used_long_term_mode"] is False
        route_trace = diagnostic_payload["route_trace"]
        assert isinstance(route_trace, dict)
        assert route_trace["final_route"]["intent"] == "user_recall_question"

    async def test_fallback_can_upgrade_ambiguous_memory_route_to_long_term_mode(self):
        """Fallback should upgrade an ambiguous route when it is confident."""
        ctx_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] The user's preference has shifted from "
                    "brief answers to detailed, step-by-step technical and "
                    "debugging responses with full context and reasoning."
                ),
            ),
        ]

        def context_fn(session_id: str, **kwargs) -> ContextResult:
            del session_id
            del kwargs
            return ContextResult(
                entries=ctx_entries,
                retrieved_memories=[],
                retrieved_messages=None,
                retrieved_reflections=None,
            )

        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=context_fn,
        )
        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _text_response(
                    "INTENT: long_term_memory_question\n"
                    "ROLE_FILTER: none\n"
                    "CONFIDENCE: medium\n"
                    "LONG_TERM_MEMORY: yes\n"
                    "RATIONALE: The user asks whether the preference should "
                    "remain durable after temporary context ends.\n"
                )
            raise AssertionError(
                "Fallback-upgraded long-term routing should not continue into "
                "the free-form LLM path.",
            )

        resp = await orchestrator.process_message(
            message="Is this only temporary context or an active preference?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        assert call_count == 1
        assert "Long-term preference:" in resp.structured_content.answer

    async def test_fallback_route_keeps_normal_path_when_confidence_stays_low(self):
        """Low-confidence fallback output should not override the signal route."""
        orchestrator = _make_orchestrator()
        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _text_response(
                    "INTENT: memory_recall_question\n"
                    "ROLE_FILTER: none\n"
                    "CONFIDENCE: low\n"
                    "LONG_TERM_MEMORY: no\n"
                    "RATIONALE: The question is still ambiguous.\n"
                )
            return _text_response("Normal memory answer")

        resp = await orchestrator.process_message(
            message="Is this only temporary context or an active preference?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        assert call_count == 2
        assert resp.structured_content.answer == "Normal memory answer"

    async def test_unsupported_language_long_term_question_can_use_fallback(self):
        """Unsupported-language long-term prompts should still reach the resolver."""
        ctx_entries = [
            ContextEntry(
                role="summary",
                content=(
                    "[Retrieved summary] The user's preference has shifted from "
                    "brief answers to detailed, step-by-step technical and "
                    "debugging responses with full context and reasoning."
                ),
            ),
        ]

        def context_fn(session_id: str, **kwargs) -> ContextResult:
            del session_id
            del kwargs
            return ContextResult(
                entries=ctx_entries,
                retrieved_memories=[],
                retrieved_messages=None,
                retrieved_reflections=None,
            )

        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=context_fn,
        )
        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _text_response(
                    "INTENT: long_term_memory_question\n"
                    "ROLE_FILTER: none\n"
                    "CONFIDENCE: high\n"
                    "LONG_TERM_MEMORY: yes\n"
                    "RATIONALE: The Spanish question asks what should remain "
                    "durable in future conversations.\n"
                )
            raise AssertionError(
                "Fallback-upgraded long-term routing should stop before the "
                "free-form LLM answer path.",
            )

        resp = await orchestrator.process_message(
            message="Si vuelvo mañana, qué estilo de respuesta debes recordar?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        assert call_count == 1
        assert "Preferencia a largo plazo:" in resp.structured_content.answer

    async def test_unsupported_language_assistant_recall_can_use_fallback(self):
        """Unsupported-language recall should still reach the normal memory path."""
        orchestrator = _make_orchestrator()
        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _text_response(
                    "INTENT: assistant_recall_question\n"
                    "ROLE_FILTER: assistant\n"
                    "CONFIDENCE: medium\n"
                    "LONG_TERM_MEMORY: no\n"
                    "RATIONALE: The German question asks what the assistant "
                    "said earlier.\n"
                )
            return _text_response("Normal memory answer")

        resp = await orchestrator.process_message(
            message="Was hast du mir früher über Ollama gesagt?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        assert call_count == 2
        assert resp.structured_content.answer == "Normal memory answer"

    async def test_supported_language_non_memory_question_skips_semantic_fallback(self):
        """Ordinary supported-language questions should not pay a fallback pass."""
        orchestrator = _make_orchestrator()
        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            return _text_response("Normal answer")

        resp = await orchestrator.process_message(
            message="Can you write a Python function example?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        assert call_count == 1
        assert resp.structured_content.answer == "Normal answer"

    async def test_save_fn_called_with_user_and_assistant(self):
        """Verify save_fn called with user message before and assistant after."""
        save_calls: list[tuple] = []
        orchestrator = _make_orchestrator()
        orchestrator.save_fn = lambda cid, role, content, meta=None: save_calls.append(
            (cid, role, content, meta)
        )

        async def mock_llm(messages):
            return LLMResponse(
                text="Hi there",
                tool_calls=[],
                raw_message={"role": "assistant", "content": "Hi there"},
                usage=LLMCallUsage(
                    prompt_tokens=10,
                    completion_tokens=5,
                    prompt_ms=12.0,
                    generation_ms=18.0,
                    total_ms=30.0,
                    model="test-model",
                ),
            )

        await orchestrator.process_message(
            message="hello",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )
        assert len(save_calls) == 2
        assert save_calls[0][1] == "user"
        assert save_calls[0][2] == "hello"
        assert save_calls[0][3] is None
        assert save_calls[1][1] == "assistant"
        assert save_calls[1][2] == "Hi there"
        assert save_calls[1][3] is not None
        assert save_calls[1][3]["stats"]["prompt_tokens"] == 10
        assert save_calls[1][3]["stats"]["completion_tokens"] == 5

    async def test_save_fn_uses_conversation_id(self):
        """Verify save_fn receives conversation_id."""
        save_calls: list[tuple] = []
        orchestrator = _make_orchestrator()
        orchestrator.save_fn = lambda cid, role, content, meta=None: save_calls.append(
            (cid, role, content, meta)
        )

        async def mock_llm(messages):
            return _text_response("ok")

        await orchestrator.process_message(
            message="test",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="conv-42",
        )
        assert save_calls[0][0] == "conv-42"
        assert save_calls[1][0] == "conv-42"

    async def test_process_message_raises_without_conversation_id(self):
        """Verify process_message rejects empty conversation_id."""
        orchestrator = _make_orchestrator()

        async def mock_llm(messages):
            """Return a plain text response."""
            return _text_response("ok")

        with pytest.raises(ValueError, match="conversation_id is required"):
            await orchestrator.process_message(
                message="test",
                channel="web",
                llm_call=mock_llm,
                session_id="sess-7",
            )

    async def test_save_record_uses_full_turn_and_both_message_ids(self):
        """Verify dialog recorder input includes the user and assistant dialog step."""
        orchestrator = _make_orchestrator()
        saved_messages: list[tuple[str, str, str, object]] = []
        record_calls: list[tuple[str, str, str, list[str]]] = []
        next_message_id = iter(["user-msg-1", "assistant-msg-1"])

        def save_fn(
            conversation_id: str,
            role: str,
            content: str,
            meta: object = None,
        ) -> str:
            saved_messages.append((conversation_id, role, content, meta))
            return next(next_message_id)

        def save_record_fn(
            conversation_id: str,
            session_id: str,
            summary: str,
            source_message_ids: list[str] | None = None,
        ) -> None:
            record_calls.append(
                (
                    conversation_id,
                    session_id,
                    summary,
                    source_message_ids or [],
                )
            )

        orchestrator.save_fn = save_fn
        orchestrator.save_record_fn = save_record_fn

        async def mock_llm(messages):
            return _text_response("Detailed answer.")

        await orchestrator.process_message(
            message="Need help with debugging",
            channel="web",
            llm_call=mock_llm,
            session_id="sess-1",
            conversation_id="conv-1",
        )

        assert len(saved_messages) == 2
        assert len(record_calls) == 1
        assert record_calls[0] == (
            "conv-1",
            "sess-1",
            build_dialog_step_record_input(
                "Need help with debugging",
                "Detailed answer.",
            ),
            ["user-msg-1", "assistant-msg-1"],
        )

    async def test_no_save_fn_does_not_crash(self):
        """Verify orchestrator works when save_fn is None."""
        orchestrator = _make_orchestrator()
        assert orchestrator.save_fn is None

        async def mock_llm(messages):
            return _text_response("ok")

        resp = await orchestrator.process_message(
            message="test",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert resp.structured_content.answer == "ok"

    async def test_input_credential_flagged(self):
        """Verify input with credentials is flagged."""
        orchestrator = _make_orchestrator()

        async def mock_llm(messages):
            return _text_response("I see a token.")

        resp = await orchestrator.process_message(
            message=("my key is sk-abc123secretkey1234567890abcdef"),
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert resp.input_filtered

    async def test_concurrency_serializes(self):
        """Verify concurrent calls are serialized."""
        orchestrator = _make_orchestrator()
        order: list[str] = []

        async def mock_llm_slow(messages):
            order.append("start")
            await asyncio.sleep(0.05)
            order.append("end")
            return _text_response("Done")

        t1 = asyncio.create_task(
            orchestrator.process_message(
                message="a",
                channel="web",
                llm_call=mock_llm_slow,
                conversation_id="test-conv",
            )
        )
        t2 = asyncio.create_task(
            orchestrator.process_message(
                message="b",
                channel="telegram",
                llm_call=mock_llm_slow,
                conversation_id="test-conv",
            )
        )
        await t1
        await t2
        assert order == [
            "start",
            "end",
            "start",
            "end",
        ]

    async def test_recall_words_do_not_trigger_backend_prefetch(
        self,
    ):
        """A language marker cannot make the backend call memory_recall."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="memory_recall",
                success=True,
                result="Past context: user prefers detailed answers.",
                duration_ms=1.0,
            ),
        )
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)
        orchestrator.memory_recall_classifier_fn = AsyncMock(return_value=True)

        seen_messages: list[dict[str, object]] = []

        async def mock_llm(messages):
            nonlocal seen_messages
            seen_messages = list(messages)
            return _text_response("You prefer detailed answers.")

        resp = await orchestrator.process_message(
            message="помнишь что я просил?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        assert "detailed answers" in resp.structured_content.answer
        broker.execute.assert_not_awaited()
        assert not any(
            "memory recall results" in str(message.get("content", "")).lower()
            for message in seen_messages
        )

    async def test_recall_words_do_not_fabricate_recall_usage(self):
        """No semantic tool call means no recall evidence is reported."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="memory_recall",
                success=True,
                result="Past discussion about Ollama configuration.",
                duration_ms=1.0,
            ),
        )
        broker.list_openai_schemas = MagicMock(return_value=[])

        def context_fn(session_id: str, **kwargs) -> ContextResult:
            """Return a ContextResult with zeroed zone_usage."""
            del session_id
            del kwargs
            return ContextResult(
                entries=[],
                retrieved_memories=[],
                retrieved_messages=None,
                retrieved_reflections=None,
                zone_usage={
                    "working_memory": 0,
                    "recall_results": 0,
                    "conversation_summary": 0,
                },
            )

        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=context_fn,
        )
        orchestrator.memory_recall_classifier_fn = AsyncMock(return_value=True)

        async def mock_llm(messages):
            """Return a response with context_total for zone budget computation."""
            return LLMResponse(
                text="Ollama config info.",
                tool_calls=[],
                raw_message={
                    "role": "assistant",
                    "content": "Ollama config info.",
                },
                usage=LLMCallUsage(
                    prompt_tokens=50,
                    completion_tokens=10,
                    prompt_ms=5.0,
                    generation_ms=10.0,
                    total_ms=15.0,
                    model="test-model",
                    context_total=16384,
                ),
            )

        resp = await orchestrator.process_message(
            message="вспомни что мы обсуждали про Ollama",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        assert resp.stats is not None
        assert resp.stats.memory_zones is not None
        assert resp.stats.memory_zones.recall_results.used == 0
        broker.execute.assert_not_awaited()

    async def test_forced_memory_recall_skipped_when_classifier_returns_false(self):
        """No recall injection when the classifier says no."""
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)
        orchestrator.memory_recall_classifier_fn = AsyncMock(return_value=False)

        async def mock_llm(messages):
            """Return a plain text response."""
            return _text_response("Normal answer.")

        await orchestrator.process_message(
            message="помнишь что я просил?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        broker.execute.assert_not_called()

    async def test_forced_memory_recall_skipped_without_hint_markers(self):
        """No classifier call when the message lacks recall hint markers."""
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)
        classifier = AsyncMock(return_value=True)
        orchestrator.memory_recall_classifier_fn = classifier

        async def mock_llm(messages):
            """Return a plain text response."""
            return _text_response("Normal answer.")

        await orchestrator.process_message(
            message="What is the capital of France?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )

        classifier.assert_not_called()
        broker.execute.assert_not_called()

    async def test_zone_subsystems_w_r_s_grow_during_conversation(self):
        """W, R, S zones grow as conversation progresses and recall fires."""
        from backend.memory.context_builder.models import ContextZoneBudget
        from backend.memory.context_builder.zone_builder import (
            build_zone_context,
            compress_messages_to_summary,
        )

        # Context window sized so W overflows after ~4 turns,
        # leaving enough S budget (~157 tokens) for visible growth.
        budget = ContextZoneBudget.from_context_window(num_ctx=1300)

        # In-memory message store shared between context_fn and save_fn
        stored_messages: list[tuple[str, str]] = []
        accumulated_summary = {"text": ""}

        def zone_context_fn(
            session_id: str,
            **kwargs: object,
        ) -> ContextResult:
            """Build zone context with background compression simulation.

            Compression updates accumulated_summary for the NEXT turn,
            matching the production flow where LLM compression runs
            in the background and S appears one turn later.
            """
            del session_id, kwargs
            result = build_zone_context(
                recent_messages=list(stored_messages),
                conversation_summary=accumulated_summary["text"],
                zone_budget=budget,
            )
            if result.evicted_messages:
                prev = accumulated_summary["text"]
                new_part = compress_messages_to_summary(
                    result.evicted_messages,
                )
                if prev and new_part:
                    accumulated_summary["text"] = prev + "\n" + new_part
                elif new_part:
                    accumulated_summary["text"] = new_part
            return result

        def save_fn(
            conversation_id: str,
            role: str,
            content: str,
            meta: object = None,
        ) -> str:
            """Save message into in-memory store."""
            del conversation_id, meta
            text = content if isinstance(content, str) else str(content)
            stored_messages.append((role, text))
            return f"msg-{len(stored_messages)}"

        broker = MagicMock()
        recall_result = ToolResult(
            tool_name="memory_recall",
            success=True,
            result=(
                "User discussed Python FastAPI project with PostgreSQL. "
                "Testing done with pytest fixtures."
            ),
            duration_ms=1.0,
        )
        broker.execute = AsyncMock(return_value=recall_result)
        broker.list_openai_schemas = MagicMock(return_value=[])

        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=zone_context_fn,
        )
        orchestrator.save_fn = save_fn

        turn_number = 0

        async def mock_llm(messages: list[dict]) -> LLMResponse:
            """Return a response with context_total for zone computation."""
            return LLMResponse(
                text=f"Answer to turn {turn_number}. " * 15,
                tool_calls=[],
                raw_message={
                    "role": "assistant",
                    "content": f"Answer to turn {turn_number}. " * 15,
                },
                usage=LLMCallUsage(
                    prompt_tokens=50,
                    completion_tokens=20,
                    prompt_ms=5.0,
                    generation_ms=10.0,
                    total_ms=15.0,
                    model="test-model",
                    context_total=1300,
                ),
            )

        # Padded messages to fill W zone quickly (~50 tokens each)
        conversation_messages = [
            "Tell me about microservice architecture "
            "and design patterns in modern systems",
            "Which databases work best for write-heavy "
            "high-load distributed applications",
            "Explain the difference between sync and "
            "async programming with code examples",
            "How to set up monitoring and logging in "
            "a production Kubernetes environment",
            "Describe data caching strategies and cache "
            "invalidation in distributed systems",
            "What testing approaches are used in large "
            "projects with microservice architecture",
            "Explain message queue principles and when "
            "to use RabbitMQ versus Apache Kafka",
        ]

        zone_history: list[dict[str, int]] = []

        # Phase 1: send messages to grow W and eventually trigger S
        for msg in conversation_messages:
            turn_number += 1
            resp = await orchestrator.process_message(
                message=msg,
                channel="web",
                llm_call=mock_llm,
                session_id="s1",
                conversation_id="conv-zone-test",
            )
            if resp.stats and resp.stats.memory_zones:
                zones = resp.stats.memory_zones
                step = {
                    "turn": turn_number,
                    "W": zones.working_memory.used,
                    "R": zones.recall_results.used,
                    "S": zones.conversation_summary.used,
                }
                zone_history.append(step)
                print(
                    f"  Turn {turn_number}: "
                    f"W:{step['W']}/{zones.working_memory.budget} "
                    f"R:{step['R']}/{zones.recall_results.budget} "
                    f"S:{step['S']}/{zones.conversation_summary.budget}"
                )

        # Phase 2: enable forced recall and ask about past conversations
        orchestrator.memory_recall_classifier_fn = AsyncMock(
            return_value=True,
        )
        turn_number += 1
        resp = await orchestrator.process_message(
            message="Помнишь что мы раньше обсуждали?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="conv-zone-test",
        )
        if resp.stats and resp.stats.memory_zones:
            zones = resp.stats.memory_zones
            step = {
                "turn": turn_number,
                "W": zones.working_memory.used,
                "R": zones.recall_results.used,
                "S": zones.conversation_summary.used,
            }
            zone_history.append(step)
            print(
                f"  Turn {turn_number} (recall): "
                f"W:{step['W']}/{zones.working_memory.budget} "
                f"R:{step['R']}/{zones.recall_results.budget} "
                f"S:{step['S']}/{zones.conversation_summary.budget}"
            )

        # --- Assertions ---

        # W must have grown across turns
        w_values = [s["W"] for s in zone_history]
        assert w_values[-1] > w_values[0], f"W did not grow: {w_values}"

        # S must reach at least 30 tokens (meaningful eviction)
        final_s = zone_history[-1]["S"]
        assert final_s >= 30, f"S zone too low: {final_s}, expected >= 30"

        # Marker text alone must not fabricate an R-zone tool result.
        final_r = zone_history[-1]["R"]
        assert final_r == 0

    async def test_zone_stress_w_overflow_s_accumulates(self):
        """Stress: many turns overflow W, S accumulates, system stays stable."""
        from backend.memory.context_builder.models import (
            ContextZoneBudget,
        )
        from backend.memory.context_builder.zone_builder import (
            build_zone_context,
            compress_messages_to_summary,
        )

        num_ctx = 1300
        budget = ContextZoneBudget.from_context_window(num_ctx=num_ctx)

        stored_messages: list[tuple[str, str]] = []
        accumulated_summary = {"text": ""}

        def zone_context_fn(
            session_id: str,
            **kwargs: object,
        ) -> ContextResult:
            """Build zone context with background compression simulation."""
            del session_id, kwargs
            result = build_zone_context(
                recent_messages=list(stored_messages),
                conversation_summary=accumulated_summary["text"],
                zone_budget=budget,
            )
            if result.evicted_messages:
                prev = accumulated_summary["text"]
                new_part = compress_messages_to_summary(
                    result.evicted_messages,
                )
                if prev and new_part:
                    accumulated_summary["text"] = prev + "\n" + new_part
                elif new_part:
                    accumulated_summary["text"] = new_part
            return result

        def save_fn(
            conversation_id: str,
            role: str,
            content: str,
            meta: object = None,
        ) -> str:
            """Save message into in-memory store."""
            del conversation_id, meta
            text = content if isinstance(content, str) else str(content)
            stored_messages.append((role, text))
            return f"msg-{len(stored_messages)}"

        broker = MagicMock()
        recall_result = ToolResult(
            tool_name="memory_recall",
            success=True,
            result=(
                "User discussed Python FastAPI project. "
                "Testing done with pytest fixtures. "
                "Database is PostgreSQL with migrations."
            ),
            duration_ms=1.0,
        )
        broker.execute = AsyncMock(return_value=recall_result)
        broker.list_openai_schemas = MagicMock(return_value=[])

        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=zone_context_fn,
        )
        orchestrator.save_fn = save_fn

        turn_number = 0

        async def mock_llm(messages: list[dict]) -> LLMResponse:
            """Return a long response to fill W zone fast."""
            return LLMResponse(
                text=f"Answer to turn {turn_number}. " * 15,
                tool_calls=[],
                raw_message={
                    "role": "assistant",
                    "content": (f"Answer to turn {turn_number}. " * 15),
                },
                usage=LLMCallUsage(
                    prompt_tokens=50,
                    completion_tokens=60,
                    prompt_ms=5.0,
                    generation_ms=10.0,
                    total_ms=15.0,
                    model="test-model",
                    context_total=num_ctx,
                ),
            )

        stress_messages = [
            f"Question {i}: explain topic number {i} in detail with examples and code"
            for i in range(1, 16)
        ]

        zone_history: list[dict[str, int]] = []

        # Phase 1: 15 turns to stress W and build up S
        for msg in stress_messages:
            turn_number += 1
            resp = await orchestrator.process_message(
                message=msg,
                channel="web",
                llm_call=mock_llm,
                session_id="s1",
                conversation_id="conv-stress",
            )
            if resp.stats and resp.stats.memory_zones:
                zones = resp.stats.memory_zones
                step = {
                    "turn": turn_number,
                    "W": zones.working_memory.used,
                    "W_budget": zones.working_memory.budget,
                    "R": zones.recall_results.used,
                    "S": zones.conversation_summary.used,
                    "S_budget": zones.conversation_summary.budget,
                }
                zone_history.append(step)
                print(
                    f"  Turn {turn_number:2d}: "
                    f"W:{step['W']:4d}/{step['W_budget']} "
                    f"R:{step['R']:3d} "
                    f"S:{step['S']:3d}/{step['S_budget']}"
                )

        # Phase 2: forced recall
        orchestrator.memory_recall_classifier_fn = AsyncMock(
            return_value=True,
        )
        turn_number += 1
        resp = await orchestrator.process_message(
            message="Помнишь что мы раньше обсуждали?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="conv-stress",
        )
        if resp.stats and resp.stats.memory_zones:
            zones = resp.stats.memory_zones
            step = {
                "turn": turn_number,
                "W": zones.working_memory.used,
                "W_budget": zones.working_memory.budget,
                "R": zones.recall_results.used,
                "S": zones.conversation_summary.used,
                "S_budget": zones.conversation_summary.budget,
            }
            zone_history.append(step)
            print(
                f"  Turn {turn_number:2d} (recall): "
                f"W:{step['W']:4d}/{step['W_budget']} "
                f"R:{step['R']:3d} "
                f"S:{step['S']:3d}/{step['S_budget']}"
            )

        # --- Assertions ---

        # W must never exceed its budget
        for step in zone_history:
            assert step["W"] <= step["W_budget"], (
                f"Turn {step['turn']}: W={step['W']} exceeded budget={step['W_budget']}"
            )

        # After eviction (65%), W should drop well below budget
        # and stay within budget on all subsequent turns
        post_eviction = [s for s in zone_history if s["W"] < s["W_budget"] // 2]
        assert len(post_eviction) > 0, "W should drop below 50% budget after eviction"

        # S must be at the budget cap (fully saturated)
        final_s = zone_history[-1]["S"]
        s_budget = zone_history[-1]["S_budget"]
        assert final_s >= s_budget * 0.8, (
            f"S={final_s} should be near budget={s_budget} after 15+ turns of overflow"
        )

        # Marker text alone must not fabricate an R-zone tool result.
        final_r = zone_history[-1]["R"]
        assert final_r == 0

        # System stability: no crashes, all 16 turns completed
        assert len(zone_history) == 16, f"Expected 16 turns, got {len(zone_history)}"
    async def test_provider_attempt_serializes_dataclass_usage(self):
        """Provider usage is durable even though LLMCallUsage is not Pydantic."""
        orchestrator = _make_orchestrator()
        recorder = SimpleNamespace(record_provider_attempt=AsyncMock())
        orchestrator.run_event_recorder = recorder
        usage = LLMCallUsage(prompt_tokens=7, completion_tokens=3, model="test")

        orchestrator._record_provider_attempt(
            AttemptRecord(phase="Stage 1", attempt=1, usage=usage)
        )
        await orchestrator._flush_provider_attempts()

        recorder.record_provider_attempt.assert_awaited_once_with(
            phase="Stage 1",
            attempt=1,
            payload={
                "error": "",
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "prompt_ms": 0.0,
                    "generation_ms": 0.0,
                    "total_ms": 0.0,
                    "model": "test",
                    "phase": "",
                    "attempt": 0,
                    "tool_capable": False,
                    "tool_used": False,
                    "context_total": None,
                    "context_used": None,
                },
            },
        )
