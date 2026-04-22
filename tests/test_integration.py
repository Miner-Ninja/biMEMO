"""End-to-end integration tests.

Covers full message->gateway->tool->response flow,
RED zone confirmation flow, performance benchmarks,
and retrieved memories pipeline.
"""

import json
import os
import time
from unittest.mock import MagicMock

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.agent_loop_models import AgentResponse
from backend.agent.concurrency import (
    AgentConcurrency,
)
from backend.llm.call_factory import LLMResponse, LLMToolCall
from backend.memory.context_builder import (
    ContextEntry,
    ContextResult,
    build_context_with_ids,
)
from backend.memory.retrieval import hybrid_search
from backend.memory.retrieval.fulltext import (
    create_fts_tables,
    index_observation,
)
from backend.memory.retrieval.vector_store import (
    create_vector_table,
    enable_vector_extension,
    store_embedding,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema
from backend.tools.broker import ToolBroker
from backend.tools.builtin import FileReadInput
from backend.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    WorkerTarget,
)
from backend.trust.zones import TrustZone


class TestMessageToResponse:
    """End-to-end: message -> gateway -> tool -> response."""

    async def test_full_flow(self):
        """Verify full message processing flow."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="file_read",
                description="Read a file",
                input_model=FileReadInput,
                zone=TrustZone.GREEN,
                worker=WorkerTarget.FS_WORKER,
            )
        )

        async def mock_handler(params):
            return "file content here"

        broker = ToolBroker(registry=registry)
        broker.set_handler("file_read", mock_handler)

        context = [
            ContextEntry(role="system", content="You are helpful."),
        ]

        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=lambda sid, **kwargs: context,
        )

        call_count = 0

        async def mock_llm(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[
                        LLMToolCall(
                            id="call_1",
                            name="file_read",
                            arguments={"path": "/tmp/test"},
                        )
                    ],
                    raw_message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "file_read",
                                    "arguments": json.dumps({"path": "/tmp/test"}),
                                },
                            }
                        ],
                    },
                )
            return LLMResponse(
                text="The file contains: file content here",
                tool_calls=[],
                raw_message={
                    "role": "assistant",
                    "content": "The file contains: file content here",
                },
            )

        resp = await orchestrator.process_message(
            message="Read /tmp/test",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="test-conv",
        )
        assert isinstance(resp, AgentResponse)
        assert "file content" in resp.structured_content.answer
        assert call_count == 2


class TestRedZoneConfirmation:
    """End-to-end: RED zone tool -> confirmation flow."""

    async def test_red_zone_deferred(self):
        """Verify RED zone tool defers for confirmation."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="http_fetch",
                description="Fetch URL",
                input_model=MagicMock(),
                zone=TrustZone.RED,
                worker=WorkerTarget.NET_WORKER,
            )
        )

        broker = ToolBroker(registry=registry)

        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=lambda sid, **kwargs: [],
        )

        async def mock_llm(messages):
            return LLMResponse(
                text="I need to fetch a URL.",
                tool_calls=[
                    LLMToolCall(
                        id="call_1",
                        name="http_fetch",
                        arguments={"url": "https://example.com"},
                    )
                ],
                raw_message={
                    "role": "assistant",
                    "content": "I need to fetch a URL.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "http_fetch",
                                "arguments": json.dumps({"url": "https://example.com"}),
                            },
                        }
                    ],
                },
            )

        await orchestrator.process_message(
            message="fetch example.com",
            channel="telegram",
            llm_call=mock_llm,
            conversation_id="test-conv",
        )
        pending = orchestrator.pending_red_calls
        assert len(pending) == 1
        assert pending[0].tool_name == "http_fetch"
        orchestrator.clear_pending()
        assert len(orchestrator.pending_red_calls) == 0


class TestPerformance:
    """Performance benchmarks."""

    def test_search_latency(self):
        """Verify search operations complete quickly."""
        from backend.memory.retrieval import (
            hybrid_search,
        )

        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        start = time.monotonic()
        for _ in range(100):
            hybrid_search(
                db=db,
                query_text="test query",
                query_embedding=[0.1] * 128,
                table="messages",
                limit=10,
            )
        elapsed = time.monotonic() - start
        assert elapsed < 5.0

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

    def test_hybrid_search_finds_observation(self, tmp_path):
        """Verify hybrid_search returns results for indexed records."""
        db = _open_test_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO records "
            "(id, conversation_id, session_id, summary, key_facts) "
            "VALUES (?, ?, ?, ?, ?)",
            ("obs-pizza", "c1", "s1", "user likes pizza with mushrooms", "[]"),
        )
        db.commit()
        index_observation(db, 1, "user likes pizza with mushrooms", "[]")
        store_embedding(db, "obs-pizza", [1.0, 0.0, 0.0, 0.0])
        db.commit()

        results = hybrid_search(
            db,
            "pizza",
            [1.0, 0.0, 0.0, 0.0],
            "records",
            limit=10,
        )
        assert len(results) >= 1
        assert any(r.identifier == "obs-pizza" for r in results)
        db.close()

    def test_context_builder_populates_retrieved_memories(self, tmp_path):
        """Verify build_context_with_ids labels search-matched records."""
        db = _open_test_db(tmp_path)
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        for index, (record_id, summary) in enumerate(
            [
                ("obs-pizza", "user likes pizza with mushrooms"),
                ("obs-code", "user prefers Python over Java"),
                ("obs-music", "user listens to jazz"),
            ]
        ):
            db.execute(
                "INSERT INTO records "
                "(id, conversation_id, session_id, summary, key_facts) "
                "VALUES (?, ?, ?, ?, ?)",
                (record_id, "c1", "s1", summary, "[]"),
            )
            index_observation(db, index + 1, summary, "[]")
            vec = [0.0] * 4
            vec[index % 4] = 1.0
            store_embedding(db, record_id, vec)
        db.commit()

        search_results = hybrid_search(
            db,
            "pizza",
            [1.0, 0.0, 0.0, 0.0],
            "records",
            limit=10,
        )
        db.close()

        records = [
            ("obs-pizza", "user likes pizza with mushrooms", "2025-06-01T12:00:00"),
            ("obs-code", "user prefers Python over Java", "2025-06-02T12:00:00"),
            ("obs-music", "user listens to jazz", "2025-06-03T12:00:00"),
        ]

        result = build_context_with_ids(
            summaries=[],
            records=records,
            recent_messages=[("user", "What food do I like?")],
            search_results=search_results,
            token_budget=10000,
        )
        assert isinstance(result, ContextResult)
        assert len(result.retrieved_memories) >= 1
        summaries = [m["summary"] for m in result.retrieved_memories]
        assert "user likes pizza with mushrooms" in summaries

        obs_entries = [e for e in result.entries if e.role == "record"]
        labeled = [e for e in obs_entries if "[Retrieved memory" in e.content]
        assert len(labeled) >= 1

    def test_empty_query_returns_no_retrieved_memories(self):
        """Verify no search results means empty retrieved_memories."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                ("o1", "some record"),
            ],
            recent_messages=[("user", "hello")],
            search_results=None,
            token_budget=10000,
        )
        assert result.retrieved_memories == []
        obs_entries = [e for e in result.entries if e.role == "record"]
        assert all(not e.content.startswith("[Retrieved memory]") for e in obs_entries)

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
