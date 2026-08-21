"""Tests that phrase markers and classifiers do not force backend prefetch."""

from unittest.mock import AsyncMock, MagicMock

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.concurrency import AgentConcurrency
from backend.llm.call_factory import LLMResponse
from backend.llm.usage import LLMCallUsage
from backend.memory.context_builder import ContextResult
from backend.tools.broker import ToolResult

from .agent_loop_fakes import _make_orchestrator, _text_response


class TestAgentToolPreflight:
    """Tests for backend prefetch and classifier gating."""

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
        orchestrator.stream_queue = object()

        llm_call_count = 0

        async def mock_llm(
            messages,
            stream_queue_override=None,
            *,
            tools_override=None,
        ):
            del messages
            del tools_override
            nonlocal llm_call_count
            llm_call_count += 1
            assert stream_queue_override is orchestrator.stream_queue
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
