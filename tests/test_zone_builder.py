"""Tests for zone-based context builder and memory pruning.

Covers build_zone_context assembly with messages, summaries, and recall
results. Tests eviction logic, message compression, LLM compression with
fallback, iterative update, and budget adherence. Also covers pruning
functions that delete stale summaries and unlinked records via DB proxy.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from backend.memory.context_builder.constants import estimate_tokens
from backend.memory.context_builder.models import ContextZoneBudget
from backend.memory.context_builder.zone_builder import (
    build_zone_context,
    compress_evicted_with_llm,
    compress_messages_to_summary,
    evict_oldest_messages,
)
from backend.memory.storage.pruning import (
    RECORD_PRUNE_AGE_DAYS,
    SUMMARY_PRUNE_AGE_DAYS,
    prune_stale_summaries,
    prune_unlinked_records,
    run_pruning_cycle,
)


def _make_budget(
    working: int = 500,
    recall: int = 200,
    summary: int = 100,
    reserve: int = 512,
    total: int = 800,
) -> ContextZoneBudget:
    """Build a ContextZoneBudget with explicit token values."""
    return ContextZoneBudget(
        working_memory=working,
        recall_results=recall,
        conversation_summary=summary,
        response_reserve=reserve,
        total_available=total,
    )


# ── build_zone_context ──────────────────────────────────────────────


class TestBuildZoneContext:
    """Tests for build_zone_context assembly."""

    def test_build_zone_context_with_messages_only(self) -> None:
        """Recent messages appear as entries when no summary or recall given."""
        messages = [("user", "Hello"), ("assistant", "Hi there")]
        budget = _make_budget()
        result = build_zone_context(
            recent_messages=messages,
            zone_budget=budget,
        )
        roles = [e.role for e in result.entries]
        assert "user" in roles
        assert "assistant" in roles
        assert "summary" not in roles
        assert "recall" not in roles

    def test_build_zone_context_with_summary(self) -> None:
        """Conversation summary appears as first entry before messages."""
        messages = [("user", "What time is it?")]
        budget = _make_budget()
        result = build_zone_context(
            recent_messages=messages,
            conversation_summary="[Earlier] User asked about the time.",
            zone_budget=budget,
        )
        assert result.entries[0].role == "summary"
        assert "[Earlier]" in result.entries[0].content

    def test_build_zone_context_with_recall(self) -> None:
        """Recall results appear in entries when provided."""
        messages = [("user", "Tell me about X")]
        budget = _make_budget()
        result = build_zone_context(
            recent_messages=messages,
            recall_results="Memory: X is a concept discussed on March 1.",
            zone_budget=budget,
        )
        roles = [e.role for e in result.entries]
        assert "recall" in roles
        recall_entry = next(e for e in result.entries if e.role == "recall")
        assert "X is a concept" in recall_entry.content

    def test_build_zone_context_respects_budget(self) -> None:
        """Entries fit within zone budgets; excess messages are evicted."""
        # Each message is ~25 tokens (100 chars / 4). Budget = 30 tokens
        # for working memory, so only 1 message should fit.
        long_msg = "a" * 100
        messages = [
            ("user", long_msg),
            ("assistant", long_msg),
            ("user", long_msg),
        ]
        budget = _make_budget(working=30, total=200)
        result = build_zone_context(
            recent_messages=messages,
            zone_budget=budget,
        )
        message_entries = [e for e in result.entries if e.role in ("user", "assistant")]
        # Only the newest message(s) that fit within 30 tokens
        assert len(message_entries) < len(messages)
        total_tokens = sum(estimate_tokens(e.content) for e in result.entries)
        assert total_tokens <= budget.total_available


# ── evict_oldest_messages ────────────────────────────────────────────


class TestEvictOldestMessages:
    """Tests for evict_oldest_messages splitting logic."""

    def test_evict_oldest_messages_keeps_newest(self) -> None:
        """Newest messages are kept, oldest are evicted when over budget."""
        messages = [
            ("user", "old message one " * 10),
            ("assistant", "old reply " * 10),
            ("user", "recent question"),
            ("assistant", "recent answer"),
        ]
        # Budget fits only the two short recent messages
        budget = 20
        kept, evicted = evict_oldest_messages(messages, budget)
        assert len(kept) > 0
        assert len(evicted) > 0
        # Kept messages are the tail (newest)
        assert kept[-1] == messages[-1]
        # Evicted messages are the head (oldest)
        assert evicted[0] == messages[0]

    def test_evict_oldest_messages_empty(self) -> None:
        """Empty list returns two empty lists."""
        kept, evicted = evict_oldest_messages([], 100)
        assert kept == []
        assert evicted == []


# ── compress_messages_to_summary ─────────────────────────────────────


class TestCompressMessagesToSummary:
    """Tests for compress_messages_to_summary extraction."""

    def test_compress_messages_to_summary(self) -> None:
        """Produces one-line summary with user and assistant parts."""
        messages = [
            ("user", "What is the capital of France?"),
            ("assistant", "The capital of France is Paris."),
            ("user", "And Germany?"),
            ("assistant", "The capital of Germany is Berlin."),
        ]
        result = compress_messages_to_summary(messages)
        assert result.startswith("[Earlier]")
        assert "User asked:" in result
        assert "Assistant:" in result
        assert "France" in result
        assert "Paris" in result

    def test_compress_messages_empty(self) -> None:
        """Empty list returns empty string."""
        assert compress_messages_to_summary([]) == ""


# ── build_zone_context eviction output ───────────────────────────────


class TestBuildZoneContextEviction:
    """Tests for evicted_messages returned by build_zone_context."""

    def test_evicted_messages_returned_on_overflow(self) -> None:
        """Evicted messages are available in the result for external compression."""
        long_msg = "a" * 100
        messages = [
            ("user", long_msg),
            ("assistant", long_msg),
            ("user", "recent"),
        ]
        budget = _make_budget(working=30, total=200)
        result = build_zone_context(
            recent_messages=messages,
            zone_budget=budget,
        )
        assert result.evicted_messages is not None
        assert len(result.evicted_messages) > 0

    def test_no_evicted_messages_when_all_fit(self) -> None:
        """No eviction when all messages fit within W budget."""
        messages = [("user", "hi"), ("assistant", "hello")]
        budget = _make_budget(working=500, total=800)
        result = build_zone_context(
            recent_messages=messages,
            zone_budget=budget,
        )
        assert result.evicted_messages is None


# ── compress_evicted_with_llm ────────────────────────────────────────


@dataclass
class _MockLLMResponse:
    """Minimal mock for LLM response with .text attribute."""

    text: str


@dataclass
class _MockProviderResponse:
    """Minimal mock for provider.chat responses with .content."""

    content: str


class TestCompressEvictedWithLLM:
    """Tests for LLM-based S-zone compression."""

    @pytest.mark.asyncio
    async def test_llm_compression_returns_structured_summary(
        self,
    ) -> None:
        """LLM call produces a structured summary from evicted messages."""
        structured_response = (
            "Goal: User wants to set up a PostgreSQL database\n"
            "Progress: Discussed schema design\n"
            "Decisions: Chose PostgreSQL over MySQL"
        )

        async def mock_llm_call(messages, max_tokens=200):
            """Return a structured summary response."""
            return _MockLLMResponse(text=structured_response)

        messages = [
            ("user", "I need a database for my project"),
            ("assistant", "PostgreSQL is a good choice"),
            ("user", "Let's design the schema"),
            ("assistant", "Here is a basic schema"),
        ]

        result = await compress_evicted_with_llm(
            messages,
            "",
            mock_llm_call,
        )

        assert "Goal:" in result
        assert "PostgreSQL" in result

    @pytest.mark.asyncio
    async def test_llm_compression_passes_system_prompt(self) -> None:
        """Verify system prompt contains structured template instructions."""
        captured_messages: list[list[dict]] = []

        async def mock_llm_call(messages, max_tokens=200):
            """Capture prompt messages for inspection."""
            captured_messages.append(list(messages))
            return _MockLLMResponse(text="Goal: test")

        await compress_evicted_with_llm(
            [("user", "hello")],
            "",
            mock_llm_call,
        )

        assert len(captured_messages) == 1
        system_msg = captured_messages[0][0]
        assert system_msg["role"] == "system"
        assert "Goal:" in system_msg["content"]
        assert "Progress:" in system_msg["content"]
        assert "Decisions:" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_llm_compression_fallback_on_error(self) -> None:
        """Falls back to primitive compression when LLM call raises."""

        async def failing_llm_call(messages, max_tokens=200):
            """Simulate LLM failure."""
            raise RuntimeError("Model unavailable")

        messages = [
            ("user", "What is Python?"),
            ("assistant", "Python is a programming language."),
        ]

        result = await compress_evicted_with_llm(
            messages,
            "",
            failing_llm_call,
        )

        assert result.startswith("[Earlier]")
        assert "Python" in result

    @pytest.mark.asyncio
    async def test_llm_compression_reads_provider_content_field(
        self,
    ) -> None:
        """Provider chat responses with .content are accepted directly."""

        async def provider_style_llm_call(messages, max_tokens=200):
            """Return a provider-style content response."""
            return _MockProviderResponse(
                content="Goal: Explain Rust\nProgress: Discussed ownership",
            )

        result = await compress_evicted_with_llm(
            [("user", "Tell me about Rust")],
            "",
            provider_style_llm_call,
        )

        assert "Goal:" in result
        assert "Rust" in result

    @pytest.mark.asyncio
    async def test_llm_compression_fallback_on_empty_response(
        self,
    ) -> None:
        """Falls back when LLM returns empty text."""

        async def empty_llm_call(messages, max_tokens=200):
            """Return an empty response."""
            return _MockLLMResponse(text="")

        messages = [
            ("user", "Tell me about Rust"),
            ("assistant", "Rust is a systems language."),
        ]

        result = await compress_evicted_with_llm(
            messages,
            "",
            empty_llm_call,
        )

        assert result.startswith("[Earlier]")
        assert "Rust" in result

    @pytest.mark.asyncio
    async def test_llm_compression_fallback_preserves_existing_summary(
        self,
    ) -> None:
        """Fallback appends primitive compression to the existing summary."""

        async def empty_llm_call(messages, max_tokens=200):
            """Return an empty response."""
            return _MockLLMResponse(text="")

        result = await compress_evicted_with_llm(
            [("user", "Add FastAPI endpoints")],
            "Goal: Build a web app",
            empty_llm_call,
        )

        assert result.startswith("Goal: Build a web app")
        assert "[Earlier]" in result

    @pytest.mark.asyncio
    async def test_llm_compression_empty_messages_returns_existing(
        self,
    ) -> None:
        """Empty evicted list returns existing summary unchanged."""
        existing = "Goal: Previous work context"

        async def should_not_be_called(messages, max_tokens=200):
            """Fail if called."""
            raise AssertionError("LLM should not be called")

        result = await compress_evicted_with_llm(
            [],
            existing,
            should_not_be_called,
        )

        assert result == existing


# ── Iterative update ─────────────────────────────────────────────────


class TestCompressEvictedIterativeUpdate:
    """Tests for iterative S-zone summary update."""

    @pytest.mark.asyncio
    async def test_iterative_update_includes_existing_summary(
        self,
    ) -> None:
        """Existing summary is passed to LLM for merging."""
        captured_messages: list[list[dict]] = []

        async def mock_llm_call(messages, max_tokens=200):
            """Capture and return merged summary."""
            captured_messages.append(list(messages))
            return _MockLLMResponse(
                text=(
                    "Goal: Build a web app with PostgreSQL\n"
                    "Progress: Schema designed, now adding API"
                ),
            )

        existing = "Goal: Build a web app\nProgress: Schema designed"
        new_messages = [
            ("user", "Now let's add the REST API"),
            ("assistant", "I'll create FastAPI endpoints"),
        ]

        result = await compress_evicted_with_llm(
            new_messages,
            existing,
            mock_llm_call,
        )

        # User content should contain the existing summary
        user_msg = captured_messages[0][1]
        assert "Existing summary:" in user_msg["content"]
        assert "Build a web app" in user_msg["content"]
        # And the new conversation segment
        assert "REST API" in user_msg["content"]
        # Result should be merged
        assert "API" in result

    @pytest.mark.asyncio
    async def test_first_compression_no_update_instruction(
        self,
    ) -> None:
        """First compression (no existing summary) has no update instruction."""
        captured_messages: list[list[dict]] = []

        async def mock_llm_call(messages, max_tokens=200):
            """Capture prompt for inspection."""
            captured_messages.append(list(messages))
            return _MockLLMResponse(text="Goal: Learn Python")

        await compress_evicted_with_llm(
            [("user", "Teach me Python")],
            "",
            mock_llm_call,
        )

        user_msg = captured_messages[0][1]
        assert "Existing summary:" not in user_msg["content"]
        assert "user: Teach me Python" in user_msg["content"]


# ── Pruning functions ────────────────────────────────────────────────


class TestPruning:
    """Tests for memory pruning operations."""

    @pytest.mark.asyncio
    async def test_prune_stale_summaries_calls_operation(self) -> None:
        """Verifies correct DB operation name and params for summary pruning."""
        db_client = AsyncMock()
        db_client.send_operation.return_value = {"deleted": 5}

        count = await prune_stale_summaries(db_client)

        assert count == 5
        db_client.send_operation.assert_called_once()
        call_args = db_client.send_operation.call_args
        assert call_args[0][0] == "prune_stale_summaries"
        params = call_args[0][1]
        assert set(params["statuses"]) == {"outdated", "orphaned"}
        assert params["age_days"] == SUMMARY_PRUNE_AGE_DAYS

    @pytest.mark.asyncio
    async def test_prune_unlinked_records_calls_operation(self) -> None:
        """Verifies correct DB operation name and params for record pruning."""
        db_client = AsyncMock()
        db_client.send_operation.return_value = {"deleted": 3}

        count = await prune_unlinked_records(db_client)

        assert count == 3
        db_client.send_operation.assert_called_once_with(
            "prune_unlinked_records",
            {"age_days": RECORD_PRUNE_AGE_DAYS},
        )

    @pytest.mark.asyncio
    async def test_run_pruning_cycle_returns_counts(self) -> None:
        """Both prune functions called and counts returned as tuple."""
        db_client = AsyncMock()
        db_client.send_operation.side_effect = [
            {"deleted": 2},
            {"deleted": 7},
        ]

        summaries_deleted, records_deleted = await run_pruning_cycle(db_client)

        assert summaries_deleted == 2
        assert records_deleted == 7
        assert db_client.send_operation.call_count == 2
