"""Tests for memory routing and context injection in the agent loop."""

from unittest.mock import AsyncMock, MagicMock

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.concurrency import AgentConcurrency
from backend.memory.context_builder import ContextEntry, ContextResult
from backend.memory.evidence_chain_models import EvidenceBundle

from .agent_loop_fakes import _make_orchestrator, _text_response


class TestAgentMemoryRouting:
    """Tests for memory routing and context injection."""

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
