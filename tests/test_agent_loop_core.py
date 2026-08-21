"""Tests for core agent loop behavior."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.agent_loop_helpers import (
    build_dialog_step_record_input,
    message_allows_file_read_tools,
    message_allows_file_write_tool,
)
from backend.agent.agent_loop_models import AgentResponse
from backend.agent.concurrency import AgentConcurrency
from backend.llm.call_factory import AttemptRecord, LLMResponse
from backend.llm.capabilities import CapabilitySupport, LlmResponseFormat
from backend.llm.usage import LLMCallUsage
from backend.memory.context_builder import ContextEntry
from backend.security.content_scanning_models import FilterResult
from backend.trust.confirmation import PendingAction

from .agent_loop_fakes import _make_orchestrator, _text_response


class TestAgentLoopCore:
    """Tests for core agent loop behavior."""

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
                assert {item["function"]["name"] for item in options.tools} >= {
                    "bimemo_ask_user"
                }
                return LLMResponse(text="Hello draft")
            assert phase_override == "Stage 2"
            assert options.response_format is LlmResponseFormat.JSON_SCHEMA
            assert options.tools == []
            return LLMResponse(
                text=(
                    '{"answer":"Hello in JSON","suggestions":["Ask another question"]}'
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

    async def test_save_fn_called_with_user_and_assistant(self):
        """Verify save_fn called with user message before and assistant after."""
        save_calls: list[tuple] = []
        orchestrator = _make_orchestrator()

        def _save(cid, role, content, meta=None, client_message_id=None):
            save_calls.append((cid, role, content, meta, client_message_id))

        orchestrator.save_fn = _save

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
            client_message_id="cm-1",
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
        assert save_calls[0][4] == "cm-1"
        assert save_calls[1][4] is None

    async def test_save_fn_uses_conversation_id(self):
        """Verify save_fn receives conversation_id."""
        save_calls: list[tuple] = []
        orchestrator = _make_orchestrator()

        def _save(cid, role, content, meta=None, client_message_id=None):
            save_calls.append((cid, role, content, meta, client_message_id))

        orchestrator.save_fn = _save

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
            client_message_id: object = None,
        ) -> str:
            del client_message_id
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
