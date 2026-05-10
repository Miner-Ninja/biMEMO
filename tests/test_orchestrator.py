"""Tests for gateway orchestrator.

Covers zone splitting, tool result message building,
parallel tool execution via asyncio.gather, and the
full agentic loop with native function calling.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.agent.agent_loop import (
    FORCED_WEB_SEARCH_CLASSIFIER_PROMPT,
    AgentOrchestrator,
)
from backend.agent.agent_loop_helpers import (
    build_dialog_step_record_input,
    build_tool_result_messages,
    message_allows_file_read_tools,
    message_allows_file_write_tool,
    message_may_need_forced_web_search_classifier,
    message_requires_forced_web_search,
    request_non_empty_final_answer,
    split_by_zone,
)
from backend.agent.agent_loop_models import (
    AgentResponse,
    ToolCall,
)
from backend.agent.concurrency import (
    AgentConcurrency,
)
from backend.agent.llm_caller import LLMCaller
from backend.agent.loop import LoopState
from backend.llm.call_factory import LLMResponse, LLMToolCall
from backend.llm.structured_response import StructuredAssistantContent
from backend.llm.usage import LLMCallUsage
from backend.memory.context_builder import ContextEntry, ContextResult
from backend.memory.evidence_chain import EvidenceBundle
from backend.security.content_scanning_models import FilterResult
from backend.tools.broker import ToolResult
from backend.trust.confirmation import PendingAction
from backend.trust.zones import TrustZone


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


class TestSplitByZone:
    """Tests for zone-based tool call splitting."""

    def test_green_is_immediate(self):
        """Verify GREEN tools are immediate."""
        calls = [ToolCall(id="c1", name="file_read", parameters={})]
        immediate, red = split_by_zone(calls)
        assert len(immediate) == 1
        assert len(red) == 0

    def test_red_is_deferred(self):
        """Verify RED tools are deferred."""
        calls = [ToolCall(id="c1", name="email_send", parameters={})]
        immediate, red = split_by_zone(calls)
        assert len(immediate) == 0
        assert len(red) == 1

    def test_mixed_zones(self):
        """Verify mixed zones are split correctly."""
        calls = [
            ToolCall(id="c1", name="file_read", parameters={}),
            ToolCall(id="c2", name="email_send", parameters={}),
            ToolCall(id="c3", name="file_write", parameters={}),
        ]
        immediate, red = split_by_zone(calls)
        assert len(immediate) == 2
        assert len(red) == 1
        assert red[0].name == "email_send"


class TestSplitByZoneWithIntent:
    """Tests for split_by_zone with user_intent_tools parameter."""

    def test_red_tool_in_intent_goes_to_immediate(self):
        """Verify RED tool matching user intent executes immediately."""
        calls = [ToolCall(id="c1", name="email_send", parameters={})]
        immediate, red = split_by_zone(
            calls,
            user_intent_tools={"email_send"},
        )
        assert len(immediate) == 1
        assert len(red) == 0

    def test_red_tool_not_in_intent_goes_to_red(self):
        """Verify RED tool without user intent stays in red list."""
        calls = [ToolCall(id="c1", name="email_send", parameters={})]
        immediate, red = split_by_zone(
            calls,
            user_intent_tools={"http_fetch"},
        )
        assert len(immediate) == 0
        assert len(red) == 1

    def test_green_tool_unaffected_by_intent(self):
        """Verify GREEN tool stays immediate regardless of intent."""
        calls = [ToolCall(id="c1", name="file_read", parameters={})]
        immediate, red = split_by_zone(
            calls,
            user_intent_tools={"http_fetch"},
        )
        assert len(immediate) == 1
        assert len(red) == 0

    def test_yellow_tool_unaffected_by_intent(self):
        """Verify YELLOW tool stays immediate regardless of intent."""
        calls = [ToolCall(id="c1", name="file_write", parameters={})]
        immediate, red = split_by_zone(
            calls,
            user_intent_tools={"http_fetch"},
        )
        assert len(immediate) == 1
        assert len(red) == 0

    def test_empty_intent_same_as_no_intent(self):
        """Verify empty intent set behaves like no intent parameter."""
        calls = [
            ToolCall(id="c1", name="file_read", parameters={}),
            ToolCall(id="c2", name="http_fetch", parameters={}),
        ]
        immediate_empty, red_empty = split_by_zone(
            calls,
            user_intent_tools=set(),
        )
        immediate_none, red_none = split_by_zone(calls)
        assert len(immediate_empty) == len(immediate_none)
        assert len(red_empty) == len(red_none)


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
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )
        ]
        msgs = build_tool_result_messages(calls, results)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "call_1"
        assert msgs[0]["content"] == "content"

    def test_error_result(self):
        """Verify error result produces role:tool message."""
        calls = [ToolCall(id="call_2", name="file_read", parameters={})]
        results = [
            ToolResult(
                tool_name="file_read",
                success=False,
                result="not found",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )
        ]
        msgs = build_tool_result_messages(calls, results)
        assert msgs[0]["content"] == "not found"

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
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            ),
            ToolResult(
                tool_name="b",
                success=True,
                result="r2",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            ),
        ]
        msgs = build_tool_result_messages(calls, results)
        assert len(msgs) == 2
        assert msgs[0]["tool_call_id"] == "call_a"
        assert msgs[1]["tool_call_id"] == "call_b"


class TestFinalAnswerRecovery:
    """Tests for one extra final-answer request after tool execution."""

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
            web_search_classifier_fn=None,
            memory_recall_classifier_fn=None,
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


def _make_orchestrator(
    broker: object | None = None,
    context: list | None = None,
) -> AgentOrchestrator:
    """Create an orchestrator with test defaults."""
    if broker is None:
        broker = MagicMock()
    ctx = context if context is not None else []
    return AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda sid, **kwargs: ctx,
    )


class TestAgentOrchestrator:
    """Tests for the full orchestrator loop."""

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
            "Проанализируй этот код:\n"
            "```python\n"
            "def hello():\n"
            "    return 'world'\n"
            "```"
        )
        assert not message_allows_file_read_tools(pasted_code)
        assert message_allows_file_read_tools(
            "Проанализируй файл sandbox/CODE_RULES.md"
        )
        assert message_allows_file_read_tools(
            "Search files in app/backend for file_read"
        )

    def test_explicit_online_search_phrases_force_web_search(self):
        """Detect explicit requests to search online before the first answer."""
        assert message_requires_forced_web_search(
            "Тогда узнай в интернете какой курс биткойна"
        )
        assert message_requires_forced_web_search(
            "Please search the web for the latest BTC price"
        )
        assert not message_requires_forced_web_search("Объясни, что такое биткойн")

    def test_only_online_hints_trigger_classifier_prefilter(self):
        """Avoid the extra classifier call for ordinary questions."""
        assert message_may_need_forced_web_search_classifier(
            "Prašau paieškok internete, koks dabar bitkoino kursas"
        )
        assert message_may_need_forced_web_search_classifier(
            "Can you check online what the latest BTC price is?"
        )
        assert not message_may_need_forced_web_search_classifier(
            "Объясни, что такое биткойн"
        )

    async def test_non_english_online_search_uses_classifier_and_runs_web_search(
        self,
    ):
        """Use the classifier so online-search requests work across languages."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="web_search",
                success=True,
                result="Search results: BTC is trading at 100.",
                zone=TrustZone.YELLOW,
                duration_ms=1.0,
            )
        )
        broker.list_openai_schemas = MagicMock(return_value=[])
        orchestrator = _make_orchestrator(broker=broker)

        llm_call_count = 0
        seen_classifier_messages: list[dict[str, object]] = []
        seen_main_messages: list[dict[str, object]] = []

        async def mock_llm(messages):
            nonlocal llm_call_count, seen_classifier_messages, seen_main_messages
            llm_call_count += 1
            if llm_call_count == 1:
                seen_classifier_messages = list(messages)
                return _text_response("YES")
            seen_main_messages = list(messages)
            return _text_response("BTC is trading at 100.")

        resp = await orchestrator.process_message(
            message="Prašau paieškok internete, koks dabar bitkoino kursas",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )

        assert "BTC is trading at 100" in resp.structured_content.answer
        assert llm_call_count == 2
        assert (
            seen_classifier_messages[0]["content"]
            == FORCED_WEB_SEARCH_CLASSIFIER_PROMPT
        )
        broker.execute.assert_awaited_once_with(
            "web_search",
            {"query": "Prašau paieškok internete, koks dabar bitkoino kursas"},
        )
        assert any(
            message["role"] == "system"
            and "Web search results were fetched before the first answer"
            in str(message["content"])
            for message in seen_main_messages
        )

    async def test_online_search_classifier_does_not_stream_yes_no_into_sse(self):
        """Classifier should stay silent in SSE so YES/NO does not leak to chat."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="web_search",
                success=True,
                result="Search results: BTC is trading at 100.",
                zone=TrustZone.YELLOW,
                duration_ms=1.0,
            )
        )
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
            if llm_call_count == 1:
                assert sse_queue_override is None
                return _text_response("YES")
            assert sse_queue_override is orchestrator.sse_queue
            return _text_response("BTC is trading at 100.")

        resp = await orchestrator.process_message(
            message="Prašau paieškok internete, koks dabar bitkoino kursas",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )

        assert "BTC is trading at 100" in resp.structured_content.answer
        assert llm_call_count == 2

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

    async def test_tool_execution(self):
        """Verify tool calls are executed."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="file_read",
                success=True,
                result="file content",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )
        )
        orchestrator = _make_orchestrator(broker=broker)

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_read", {"path": "/t"}),
                    ]
                )
            return _text_response("Done reading file.")

        resp = await orchestrator.process_message(
            message="read file",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert "Done reading file" in resp.structured_content.answer
        broker.execute.assert_called_once()

    async def test_explicit_online_search_runs_web_search_before_first_llm_answer(self):
        """Run web_search early when the user explicitly asks to search online."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="web_search",
                success=True,
                result="Search results: BTC is trading at 100.",
                zone=TrustZone.YELLOW,
                duration_ms=1.0,
            )
        )
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
        broker.execute.assert_awaited_once_with(
            "web_search",
            {"query": "Тогда узнай в интернете какой курс биткойна"},
        )
        assert any(
            message["role"] == "system"
            and "Web search results were fetched before the first answer"
            in str(message["content"])
            for message in seen_messages
        )

    async def test_empty_final_answer_after_tool_use_triggers_recovery(self):
        """Recover with one final plain-text answer when tool flow ends blank."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="file_read",
                success=True,
                result="Recovered file content",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )
        )
        orchestrator = _make_orchestrator(broker=broker)

        call_count = 0

        async def mock_llm(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response([("call_1", "file_read", {"path": "/tmp/x"})])
            if call_count == 2:
                return _text_response("")
            assert any(
                message["role"] == "system"
                and "Do not return an empty reply." in message["content"]
                for message in messages
            )
            return _text_response("Recovered final answer.")

        resp = await orchestrator.process_message(
            message="read the file and answer",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )

        assert resp.structured_content.answer == "Recovered final answer."
        assert broker.execute.call_count == 1
        assert call_count == 3

    async def test_red_zone_deferred(self):
        """Verify RED zone calls are deferred."""
        orchestrator = _make_orchestrator()

        async def mock_llm(messages):
            return _tool_response(
                [
                    ("call_1", "email_send", {"to": "user@example.com"}),
                ],
                text="I need to send this email.",
            )

        await orchestrator.process_message(
            message="send email",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert len(orchestrator.pending_red_calls) == 1
        assert orchestrator.pending_red_calls[0].tool_name == "email_send"

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

    async def test_injection_flag_limits(self):
        """Verify injection flag reduces iterations."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="file_read",
                success=True,
                result="ok",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )
        )
        orchestrator = _make_orchestrator(broker=broker)

        async def mock_llm(messages):
            return _tool_response(
                [
                    ("call_1", "file_read", {}),
                ]
            )

        resp = await orchestrator.process_message(
            message="loop",
            channel="web",
            llm_call=mock_llm,
            injection_flag=True,
            conversation_id="test-conv",
        )
        assert resp.loop_result.iterations <= 10

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

    async def test_red_calls_get_real_action_ids(self):
        """Verify RED calls have cryptographic action IDs."""
        orchestrator = _make_orchestrator()

        async def mock_llm(messages):
            return _tool_response(
                [
                    ("call_1", "email_send", {"to": "user@example.com"}),
                ],
                text="Need to send.",
            )

        await orchestrator.process_message(
            message="send",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        pending = orchestrator.pending_red_calls
        assert len(pending) == 1
        assert len(pending[0].action_id) > 20
        assert pending[0].action_id != "pending"
        assert pending[0].channel == "web"

    async def test_yellow_notification_emitted(self):
        """Verify YELLOW tool pushes SSE notification."""
        queue: asyncio.Queue = asyncio.Queue()
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="file_write",
                success=True,
                result="wrote 100 bytes",
                zone=TrustZone.YELLOW,
                duration_ms=5.0,
            )
        )
        orchestrator = _make_orchestrator(broker=broker)
        orchestrator.sse_queue = queue

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_write", {"path": "/t", "content": "x"}),
                    ]
                )
            return _text_response("Done.")

        await orchestrator.process_message(
            message="write",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        event = next(item for item in events if item["type"] == "notification")
        assert event["type"] == "notification"
        assert event["tool"] == "file_write"

    async def test_green_no_notification(self):
        """Verify GREEN tool does not push notification."""
        queue: asyncio.Queue = asyncio.Queue()
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="file_read",
                success=True,
                result="content",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )
        )
        orchestrator = _make_orchestrator(broker=broker)
        orchestrator.sse_queue = queue

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_read", {}),
                    ]
                )
            return _text_response("Done.")

        await orchestrator.process_message(
            message="read",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert all(event["type"] == "tool_status" for event in events)

    async def test_no_queue_skips_notification(self):
        """Verify None sse_queue does not crash."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="file_write",
                success=True,
                result="ok",
                zone=TrustZone.YELLOW,
                duration_ms=1.0,
            )
        )
        orchestrator = _make_orchestrator(broker=broker)
        assert orchestrator.sse_queue is None

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_write", {"path": "/t", "content": "x"}),
                    ]
                )
            return _text_response("Done.")

        resp = await orchestrator.process_message(
            message="write",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert "Done" in resp.structured_content.answer

    async def test_parallel_execution_concurrent(self):
        """Verify multiple tool calls run concurrently."""
        execution_log: list[tuple[str, str]] = []

        async def slow_execute(name, params):
            """Simulate a slow tool with timing log."""
            execution_log.append((name, "start"))
            await asyncio.sleep(0.05)
            execution_log.append((name, "end"))
            return ToolResult(
                tool_name=name,
                success=True,
                result="ok",
                zone=TrustZone.GREEN,
                duration_ms=50.0,
            )

        broker = MagicMock()
        broker.execute = AsyncMock(side_effect=slow_execute)
        orchestrator = _make_orchestrator(broker=broker)

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_read", {}),
                        ("call_2", "file_search", {}),
                    ]
                )
            return _text_response("Done.")

        start = time.monotonic()
        await orchestrator.process_message(
            message="read files and scan the workspace",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        elapsed = time.monotonic() - start

        # Parallel: should complete in ~50ms, not ~100ms
        assert elapsed < 0.15
        assert broker.execute.call_count == 2
        # Both starts should happen before any end
        starts = [i for i, (_, e) in enumerate(execution_log) if e == "start"]
        ends = [i for i, (_, e) in enumerate(execution_log) if e == "end"]
        assert starts[1] < ends[0]

    async def test_single_immediate_via_gather(self):
        """Verify single tool call works via gather."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="file_read",
                success=True,
                result="content",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )
        )
        orchestrator = _make_orchestrator(broker=broker)

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_read", {}),
                    ]
                )
            return _text_response("Done.")

        resp = await orchestrator.process_message(
            message="read",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert "Done" in resp.structured_content.answer
        broker.execute.assert_called_once()

    async def test_parallel_one_fails_others_succeed(
        self,
    ):
        """Verify one failing tool does not block others."""
        call_index = 0

        async def mixed_execute(name, params):
            """Return failure for first, success for second."""
            nonlocal call_index
            call_index += 1
            if call_index == 1:
                return ToolResult(
                    tool_name=name,
                    success=False,
                    result="error occurred",
                    zone=TrustZone.GREEN,
                    duration_ms=1.0,
                )
            return ToolResult(
                tool_name=name,
                success=True,
                result="ok",
                zone=TrustZone.GREEN,
                duration_ms=1.0,
            )

        broker = MagicMock()
        broker.execute = AsyncMock(side_effect=mixed_execute)
        orchestrator = _make_orchestrator(broker=broker)

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_read", {}),
                        ("call_2", "file_search", {}),
                    ]
                )
            return _text_response("All done.")

        resp = await orchestrator.process_message(
            message="multi",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert "All done" in resp.structured_content.answer
        assert broker.execute.call_count == 2

    async def test_parallel_mixed_yellow_notifications(
        self,
    ):
        """Verify YELLOW notifications after parallel gather."""
        queue: asyncio.Queue = asyncio.Queue()

        async def mixed_zone_execute(name, params):
            """Return GREEN for first, YELLOW for second."""
            if name == "file_read":
                return ToolResult(
                    tool_name=name,
                    success=True,
                    result="content",
                    zone=TrustZone.GREEN,
                    duration_ms=1.0,
                )
            return ToolResult(
                tool_name=name,
                success=True,
                result="wrote bytes",
                zone=TrustZone.YELLOW,
                duration_ms=5.0,
            )

        broker = MagicMock()
        broker.execute = AsyncMock(side_effect=mixed_zone_execute)
        orchestrator = _make_orchestrator(broker=broker)
        orchestrator.sse_queue = queue

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_response(
                    [
                        ("call_1", "file_read", {}),
                        ("call_2", "file_write", {"path": "/t", "content": "x"}),
                    ]
                )
            return _text_response("Done.")

        await orchestrator.process_message(
            message="read and write",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        notification_events = [
            event for event in events if event["type"] == "notification"
        ]
        assert len(notification_events) == 1
        event = notification_events[0]
        assert event["tool"] == "file_write"

    async def test_forced_memory_recall_injects_results_into_messages(
        self,
    ):
        """Recall runs early when classifier detects a past-conversation question."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="memory_recall",
                success=True,
                result="Past context: user prefers detailed answers.",
                zone=TrustZone.GREEN,
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
        broker.execute.assert_any_call(
            "memory_recall",
            {"query": "помнишь что я просил?"},
        )
        assert any(
            message["role"] == "system"
            and "Memory recall results from past conversations"
            in str(message["content"])
            for message in seen_messages
        )

    async def test_forced_memory_recall_updates_zone_usage_recall_results(self):
        """Forced recall tokens are reflected in zone_usage recall_results."""
        broker = MagicMock()
        broker.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="memory_recall",
                success=True,
                result="Past discussion about Ollama configuration.",
                zone=TrustZone.GREEN,
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
        assert resp.stats.memory_zones.recall_results.used > 0

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
            zone=TrustZone.GREEN,
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

        # R must reach at least 5 tokens (forced recall injected)
        final_r = zone_history[-1]["R"]
        assert final_r >= 5, f"R zone too low: {final_r}, expected >= 5"

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
            zone=TrustZone.GREEN,
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

        # R must be non-zero after forced recall
        final_r = zone_history[-1]["R"]
        assert final_r >= 5, f"R zone too low: {final_r}, expected >= 5"

        # System stability: no crashes, all 16 turns completed
        assert len(zone_history) == 16, f"Expected 16 turns, got {len(zone_history)}"
