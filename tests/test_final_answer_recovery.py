"""Tests for final answer recovery after tool execution."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.agent_loop_helpers import request_non_empty_final_answer
from backend.agent.finalization import (
    RequirementSatisfaction,
    ResponseRequirements,
    build_required_tool_failure_answer,
    looks_like_status_only_answer,
    validate_final_answer,
)
from backend.agent.llm_caller import LLMCaller
from backend.agent.loop import LoopState
from backend.llm.call_factory import LLMResponse, LLMToolCall
from backend.llm.structured_response import StructuredAssistantContent
from backend.llm.usage import LLMCallUsage  # noqa: TC001

from .agent_loop_fakes import _text_response


# TC001: LLMCallUsage appears only in type annotations; the import is kept
# at top level to match the pre-split module.


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
        llm_caller.stream_queue = asyncio.Queue()
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
            stream_queue = kwargs.get("stream_queue_override")
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
        assert captured_kwargs["stream_queue_override"] is not llm_caller.stream_queue
        assert await llm_caller.stream_queue.get() == {
            "type": "thinking",
            "content": "checking",
        }
        assert llm_caller.stream_queue.empty()

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
