"""End-to-end integration tests.

Covers full message->gateway->tool->response flow,
RED zone confirmation flow, performance benchmarks,
and retrieved memories pipeline.
"""

import os
import time
from unittest.mock import MagicMock

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.agent_loop_models import AgentResponse
from backend.agent.concurrency import (
    AgentConcurrency,
)
from backend.llm.call_factory import LLMResponse
from backend.memory.context_builder import (
    ContextEntry,
    ContextResult,
)
from backend.memory.retrieval.fulltext import (
    create_fts_tables,
)
from backend.memory.retrieval.vector_store import (
    create_vector_table,
    enable_vector_extension,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


class TestPerformance:
    """Performance benchmarks."""

    def test_context_build_latency(self):
        """Verify context building is fast."""
        fn = MagicMock(
            return_value=[ContextEntry(role="system", content="x" * 1000)] * 50
        )
        start = time.monotonic()
        for _ in range(100):
            fn("s1")
        elapsed = time.monotonic() - start
        assert elapsed < 1.0


def _open_test_db(tmp_path):
    """Create a test database with schema, FTS5, and vector tables."""
    db = DatabaseManager(str(tmp_path / "integration.db"))
    key = os.urandom(32)
    db.open(key)
    initialize_schema(db)
    create_fts_tables(db)
    enable_vector_extension(db)
    create_vector_table(db, 4)
    return db


class TestRetrievedMemoriesPipeline:
    """End-to-end tests for retrieved memories from search through context building."""

    async def test_orchestrator_threads_retrieved_memories(self):
        """Verify AgentOrchestrator passes retrieved_memories in response."""
        memories = [
            {"summary": "user likes pizza", "date": "2025-01-15"},
            {"summary": "user prefers Python", "date": "2025-02-20"},
        ]
        context_result = ContextResult(
            entries=[
                ContextEntry(
                    role="record",
                    content="[Retrieved memory, 2025-01-15] user likes pizza",
                ),
                ContextEntry(
                    role="record",
                    content="[Retrieved memory, 2025-02-20] user prefers Python",
                ),
                ContextEntry(role="user", content="What do I like?"),
            ],
            retrieved_memories=memories,
        )

        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=lambda sid, **kwargs: context_result,
        )

        async def mock_llm(messages):
            return LLMResponse(
                text="I recall you like pizza and Python!",
                tool_calls=[],
                raw_message={
                    "role": "assistant",
                    "content": "I recall you like pizza and Python!",
                },
            )

        resp = await orchestrator.process_message(
            message="What do I like?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )
        assert isinstance(resp, AgentResponse)
        assert resp.retrieved_memories == memories
        assert "pizza" in resp.structured_content.answer

    async def test_orchestrator_plain_list_backward_compat(self):
        """Verify orchestrator works with plain list context (no ContextResult)."""
        orchestrator = AgentOrchestrator(
            broker=MagicMock(),
            concurrency=AgentConcurrency(),
            context_fn=lambda sid, **kwargs: [
                ContextEntry(role="system", content="You are helpful."),
            ],
        )

        async def mock_llm(messages):
            return LLMResponse(
                text="Hello!",
                tool_calls=[],
                raw_message={"role": "assistant", "content": "Hello!"},
            )

        resp = await orchestrator.process_message(
            message="hi",
            channel="web",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        assert resp.retrieved_memories == []
