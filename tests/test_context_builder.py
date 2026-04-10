"""Tests for four-tier context builder.

Covers context assembly, token estimation, compression threshold,
query-aware ranking, token budget enforcement, and retrieved messages tier.
"""

from zoneinfo import ZoneInfo

import pytest
from backend.memory.context_builder import (
    ContextResult,
    build_context_with_ids,
    compute_memory_budget,
    estimate_tokens,
    should_compress,
)
from backend.memory.retrieval import SearchResult


# --- Test constants ---
TEST_TOKEN_BUDGET = 4000
TEST_COMPRESSION_THRESHOLD = 4000

# --- Inputs and expected outputs for compute_memory_budget tests ---
MEDIUM_LLM_CONTEXT_WINDOW = 16384
LARGE_LLM_CONTEXT_WINDOW = 65536
EXPECTED_BUDGET_FOR_MEDIUM_WINDOW = 4915
EXPECTED_BUDGET_FOR_LARGE_WINDOW = 12000


def build_simple_context(
    *,
    summaries: list[str],
    records: list[str],
    recent_messages: list[tuple[str, str]],
    token_budget: int | None = None,
    llm_context_window: int | None = None,
    memory_query: bool = False,
) -> list:
    """Build context entries through the runtime context-builder path."""
    result = build_context_with_ids(
        summaries=summaries,
        records=[(f"o{index}", record) for index, record in enumerate(records)],
        recent_messages=recent_messages,
        token_budget=token_budget if token_budget is not None else TEST_TOKEN_BUDGET,
        llm_context_window=llm_context_window,
        memory_query=memory_query,
    )
    return result.entries


class TestBuildContext:
    """Tests for context assembly."""

    def test_all_tiers(self):
        """Verify all three tiers are assembled in priority order."""
        ctx = build_simple_context(
            summaries=["main_point1"],
            records=["obs1", "obs2"],
            recent_messages=[
                ("user", "hello"),
                ("assistant", "hi"),
            ],
        )
        assert len(ctx) == 5
        # Messages first, then records, then summaries
        assert ctx[0].role == "user"
        assert ctx[1].role == "assistant"
        assert ctx[2].role == "record"
        assert ctx[3].role == "record"
        assert ctx[4].role == "summary"

    def test_empty_tiers(self):
        """Verify empty tiers produce empty context."""
        ctx = build_simple_context(
            summaries=[],
            records=[],
            recent_messages=[],
        )
        assert len(ctx) == 0

    def test_only_messages(self):
        """Verify context with only recent messages."""
        ctx = build_simple_context(
            summaries=[],
            records=[],
            recent_messages=[("user", "test")],
        )
        assert len(ctx) == 1
        assert ctx[0].content == "test"

    def test_backward_compatible_without_search(self):
        """Verify default call without search_results works."""
        ctx = build_simple_context(
            summaries=["r1"],
            records=["o1"],
            recent_messages=[("user", "hi")],
        )
        assert len(ctx) == 3


class TestTokenBudget:
    """Tests for token budget enforcement."""

    def test_budget_limits_observations(self):
        """Verify records are cut when budget is tight."""
        # Each "x" * 400 = 100 tokens. Messages take 100, budget is 250.
        # Only 1 record fits (150 remaining after messages).
        ctx = build_simple_context(
            summaries=[],
            records=["x" * 400, "y" * 400],
            recent_messages=[("user", "x" * 400)],
            token_budget=250,
        )
        obs = [e for e in ctx if e.role == "record"]
        assert len(obs) == 1

    def test_budget_limits_reflections(self):
        """Verify summaries are cut after records fill budget."""
        ctx = build_simple_context(
            summaries=["r" * 400],
            records=["o" * 400],
            recent_messages=[("user", "m" * 400)],
            token_budget=250,
        )
        refs = [e for e in ctx if e.role == "summary"]
        assert len(refs) == 0

    def test_messages_always_included(self):
        """Verify messages are always included even if over budget."""
        ctx = build_simple_context(
            summaries=["r1"],
            records=["o1"],
            recent_messages=[("user", "x" * 8000)],
            token_budget=100,
        )
        assert len(ctx) == 1
        assert ctx[0].role == "user"

    def test_compute_memory_budget_scales_with_context_window(self):
        """Memory budget should scale conservatively with num_ctx."""
        assert (
            compute_memory_budget(MEDIUM_LLM_CONTEXT_WINDOW)
            == EXPECTED_BUDGET_FOR_MEDIUM_WINDOW
        )
        assert (
            compute_memory_budget(LARGE_LLM_CONTEXT_WINDOW)
            == EXPECTED_BUDGET_FOR_LARGE_WINDOW
        )

        # Silent fallback for None / non-positive removed: must raise loudly.
        with pytest.raises(ValueError, match="positive integer"):
            compute_memory_budget(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="positive integer"):
            compute_memory_budget(0)
        with pytest.raises(ValueError, match="positive integer"):
            compute_memory_budget(-1)

    def test_large_context_window_allows_more_observations(self):
        """Larger num_ctx should expand memory budget when using defaults."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                ("o1", "x" * 400),
                ("o2", "y" * 400),
                ("o3", "z" * 400),
                ("o4", "w" * 400),
            ],
            recent_messages=[("user", "m" * 400)],
            llm_context_window=16384,
        )
        record_entries = [entry for entry in result.entries if entry.role == "record"]
        assert len(record_entries) >= 3

    def test_memory_query_shifts_budget_to_memory_tiers(self):
        """Memory-seeking queries should retain more record entries."""
        recent_messages = [
            ("user", "m" * 400),
            ("assistant", "n" * 400),
            ("user", "o" * 400),
        ]
        records = [
            ("o1", "x" * 400),
            ("o2", "y" * 400),
            ("o3", "z" * 400),
        ]

        regular_result = build_context_with_ids(
            summaries=[],
            records=records,
            recent_messages=recent_messages,
            token_budget=400,
            memory_query=False,
        )
        memory_result = build_context_with_ids(
            summaries=[],
            records=records,
            recent_messages=recent_messages,
            token_budget=400,
            memory_query=True,
        )

        regular_observations = [
            entry for entry in regular_result.entries if entry.role == "record"
        ]
        memory_observations = [
            entry for entry in memory_result.entries if entry.role == "record"
        ]
        assert len(memory_observations) > len(regular_observations)

    def test_large_raw_dialog_tail_becomes_dialog_summary(self):
        """Older raw dialog should collapse into one summary entry."""
        recent_messages = [
            ("user", "Need a rollout plan for the API migration." * 20),
            (
                "assistant",
                "We should stage the deploy and verify the health checks." * 20,
            ),
            ("user", "Also keep rollback steps and database backup records." * 20),
            ("assistant", "I will keep the backup and rollback flow explicit." * 20),
            ("user", "The newest detail is that the window starts at 22:00 UTC." * 20),
            (
                "assistant",
                "Understood, the maintenance window starts at 22:00 UTC." * 20,
            ),
            (
                "user",
                "The freshest constraint is that the status page must update first."
                * 20,
            ),
            (
                "assistant",
                "I will treat the status page update as the latest required step." * 20,
            ),
        ]

        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "deployment prefers explicit rollback steps",
                    [],
                    "2026-03-10T10:00:00",
                )
            ],
            records=[("o1", "remember the backup step", "2026-03-10T09:00:00")],
            recent_messages=recent_messages,
            token_budget=3200,
        )

        assert result.entries[0].role == "dialog_summary"
        assert result.entries[0].content.startswith("Earlier dialog summary:")
        roles = [entry.role for entry in result.entries]
        assert "record" in roles
        assert "summary" in roles
        assert roles.count("user") + roles.count("assistant") < len(recent_messages)

    def test_short_dialog_keeps_raw_messages_without_summary(self):
        """Short recent history should stay raw without a dialog summary entry."""
        result = build_context_with_ids(
            summaries=[],
            records=[],
            recent_messages=[
                ("user", "Short question"),
                ("assistant", "Short answer"),
            ],
            token_budget=400,
        )

        roles = [entry.role for entry in result.entries]
        assert roles == ["user", "assistant"]
        assert result.memory_guidance is None

    def test_summaries_add_compressed_memory_guidance(self):
        """Compressed summaries should add one system guidance block."""
        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "deployment prefers explicit rollback steps",
                    [],
                    "2026-03-10T10:00:00",
                )
            ],
            records=[],
            recent_messages=[("user", "What do you remember about the rollout plan?")],
            token_budget=400,
        )

        assert result.memory_guidance is not None
        assert "Compressed memory guidance:" in result.memory_guidance
        assert "stronger evidence from retrieved messages" in result.memory_guidance

    def test_dialog_summary_adds_compressed_memory_guidance(self):
        """Compressed dialog tails should add the same guidance block."""
        recent_messages = [
            ("user", "Need a rollout plan for the API migration." * 20),
            (
                "assistant",
                "We should stage the deploy and verify the health checks." * 20,
            ),
            ("user", "Also keep rollback steps and database backup records." * 20),
            ("assistant", "I will keep the backup and rollback flow explicit." * 20),
            ("user", "The newest detail is that the window starts at 22:00 UTC." * 20),
            (
                "assistant",
                "Understood, the maintenance window starts at 22:00 UTC." * 20,
            ),
            (
                "user",
                "The freshest constraint is that the status page must update first."
                * 20,
            ),
            (
                "assistant",
                "I will treat the status page update as the latest required step." * 20,
            ),
        ]

        result = build_context_with_ids(
            summaries=[],
            records=[],
            recent_messages=recent_messages,
            token_budget=3200,
        )

        assert result.entries[0].role == "dialog_summary"
        assert result.memory_guidance is not None
        assert "Compressed memory guidance:" in result.memory_guidance


class TestQueryAwareContext:
    """Tests for search-result-driven record ranking."""

    def test_ranked_observations_by_search(self):
        """Verify search-matched records come first with date label."""
        search_results = [
            SearchResult(identifier="o2", source="hybrid", score=0.03),
            SearchResult(identifier="o0", source="keyword", score=0.01),
        ]
        result = build_context_with_ids(
            summaries=[],
            records=[
                ("o0", "record zero", "2025-01-10 12:00:00"),
                ("o1", "record one", "2025-01-11T12:00:00"),
                ("o2", "record two", "2025-01-12 12:00:00"),
            ],
            recent_messages=[],
            search_results=search_results,
            token_budget=10000,
        )
        assert isinstance(result, ContextResult)
        obs = [e for e in result.entries if e.role == "record"]
        assert len(obs) == 3
        # o2 first (score 0.03), then o0 (score 0.01), then o1 (unmatched)
        assert obs[0].content == "[Retrieved memory, 2025-01-12 12:00:00] record two"
        assert obs[1].content == "[Retrieved memory, 2025-01-10 12:00:00] record zero"
        assert obs[2].content == "[2025-01-11 12:00:00] record one"
        # retrieved_memories contains dicts with summary and date
        assert result.retrieved_memories == [
            {"summary": "record two", "date": "2025-01-12 12:00:00"},
            {"summary": "record zero", "date": "2025-01-10 12:00:00"},
        ]

    def test_no_search_results_preserves_order(self):
        """Verify original order when no search results, dates in labels."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                ("o0", "first", "2025-02-01T10:00:00"),
                ("o1", "second", "2025-02-02T10:00:00"),
            ],
            recent_messages=[],
            token_budget=10000,
        )
        assert isinstance(result, ContextResult)
        obs = [e for e in result.entries if e.role == "record"]
        assert obs[0].content == "[2025-02-01 10:00:00] first"
        assert obs[1].content == "[2025-02-02 10:00:00] second"
        assert result.retrieved_memories == []

    def test_record_ranking_prefers_stronger_evidence_over_slightly_higher_score(self):
        """Better supported clean records should beat slightly stronger noisy hits."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                (
                    "o_strong",
                    "stronger evidence record",
                    ["confirmed preference"],
                    [],
                    "2026-03-20T10:00:00",
                    ["m1", "m2"],
                    False,
                ),
                (
                    "o_noisy",
                    "tainted weak record",
                    ["uncertain memory"],
                    [],
                    "2026-03-20T10:05:00",
                    [],
                    True,
                ),
            ],
            recent_messages=[],
            search_results=[
                SearchResult(identifier="o_noisy", source="hybrid", score=0.061),
                SearchResult(identifier="o_strong", source="hybrid", score=0.06),
            ],
            token_budget=10000,
        )
        obs = [entry.content for entry in result.entries if entry.role == "record"]
        assert "stronger evidence record" in obs[0]
        assert "tainted weak record" in obs[1]
        assert result.retrieved_memories == [
            {"summary": "stronger evidence record", "date": "2026-03-20 10:00:00"},
            {"summary": "tainted weak record", "date": "2026-03-20 10:05:00"},
        ]

    def test_2tuple_backward_compat(self):
        """Verify 2-tuple records still work (no date)."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                ("o0", "first"),
                ("o1", "second"),
            ],
            recent_messages=[],
            token_budget=10000,
        )
        assert isinstance(result, ContextResult)
        obs = [e for e in result.entries if e.role == "record"]
        assert obs[0].content == "first"
        assert obs[1].content == "second"
        assert result.retrieved_memories == []

    def test_structured_record_fields_are_included(self):
        """Key facts and action items should reach the prompt entry."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                (
                    "o1",
                    "deploy checklist",
                    ["use blue-green deploys"],
                    ["verify migrations before rollout"],
                    "2026-02-02T10:00:00",
                ),
            ],
            recent_messages=[],
            token_budget=10000,
        )
        record_entries = [entry for entry in result.entries if entry.role == "record"]
        assert record_entries[0].content == (
            "[2026-02-02 10:00:00] deploy checklist\n"
            "Key facts:\n"
            "- use blue-green deploys\n"
            "Action items:\n"
            "- verify migrations before rollout"
        )


class TestRetrievedMessagesTier:
    """Tests for retrieved messages tier in context assembly."""

    def test_tier_ordering(self):
        """Verify order: messages → retrieved → records → summaries."""
        result = build_context_with_ids(
            summaries=["main_point1"],
            records=[("o1", "obs1", "2025-01-01T12:00:00")],
            recent_messages=[("user", "hello")],
            token_budget=10000,
            retrieved_messages=[
                ("m99", "user", "old detail", "other-session", "2025-01-15T10:00:00"),
            ],
            session_id="current-session",
        )
        roles = [e.role for e in result.entries]
        assert roles == ["user", "retrieved_message", "record", "summary"]

    def test_empty_retrieved_messages(self):
        """Verify empty retrieved_messages behaves like old behavior."""
        result = build_context_with_ids(
            summaries=["r1"],
            records=[("o1", "obs1")],
            recent_messages=[("user", "hi")],
            token_budget=10000,
            retrieved_messages=None,
            session_id="s1",
        )
        roles = [e.role for e in result.entries]
        assert "retrieved_message" not in roles
        assert result.retrieved_messages is None

    def test_cross_session_label(self):
        """Verify cross-session messages get [Retrieved message, date] label."""
        result = build_context_with_ids(
            summaries=[],
            records=[],
            recent_messages=[],
            token_budget=10000,
            retrieved_messages=[
                ("m1", "user", "some detail", "other-session", "2025-03-10T08:00:00"),
            ],
            session_id="current-session",
        )
        assert len(result.entries) == 1
        assert (
            result.entries[0].content
            == "[Retrieved message, 2025-03-10 08:00:00] some detail"
        )
        assert result.entries[0].role == "retrieved_message"

    def test_same_session_label(self):
        """Verify same-session trimmed messages get [Earlier in conversation] label."""
        result = build_context_with_ids(
            summaries=[],
            records=[],
            recent_messages=[],
            token_budget=10000,
            retrieved_messages=[
                ("m1", "user", "trimmed detail", "s1", "2025-03-10T08:00:00"),
            ],
            session_id="s1",
        )
        assert (
            result.entries[0].content
            == "[Earlier in conversation, 2025-03-10 08:00:00] trimmed detail"
        )

    def test_budget_cascading_empty_retrieved(self):
        """Verify empty retrieved tier cascades budget to records."""
        # With no retrieved messages, records inherit the retrieved tier budget.
        # Each "x" * 400 = 100 tokens. Budget=400.
        # Messages: 50% = 200 → message uses 100, unused 100 cascades
        # Retrieved: 15% = 60 + 100 unused = 160, all cascades to records
        # Records: 25% = 100 + 160 = 260 → fits 2 records (200 tokens)
        result = build_context_with_ids(
            summaries=[],
            records=[
                ("o1", "x" * 400),
                ("o2", "y" * 400),
                ("o3", "z" * 400),
            ],
            recent_messages=[("user", "x" * 400)],
            token_budget=400,
            retrieved_messages=None,
        )
        obs = [e for e in result.entries if e.role == "record"]
        assert len(obs) >= 2  # With cascading, at least 2 should fit

    def test_retrieved_messages_debug_metadata(self):
        """Verify ContextResult.retrieved_messages contains debug metadata."""
        result = build_context_with_ids(
            summaries=[],
            records=[],
            recent_messages=[],
            token_budget=10000,
            retrieved_messages=[
                ("m1", "user", "detail content here", "c2", "2025-04-01 12:00:00"),
            ],
            session_id="c1",
        )
        assert result.retrieved_messages is not None
        assert len(result.retrieved_messages) == 1
        rm = result.retrieved_messages[0]
        assert rm["role"] == "user"
        assert rm["content"] == "detail content here"
        assert rm["conversation_id"] == "c2"
        assert rm["date"] == "2025-04-01 12:00:00"

    def test_memory_query_skips_retrieved_message_covered_by_observation(self):
        """Recall mode should skip raw hits already covered by matched memory."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                (
                    "o1",
                    "User prefers concise Russian answers",
                    ["User asked for concise replies"],
                    [],
                    "2026-03-10T10:00:00",
                ),
            ],
            recent_messages=[],
            token_budget=10000,
            retrieved_messages=[
                (
                    "m1",
                    "assistant",
                    "User prefers concise Russian answers in chat replies.",
                    "c2",
                    "2026-03-10T09:00:00",
                ),
            ],
            search_results=[
                SearchResult(identifier="o1", source="keyword", score=0.05),
            ],
            memory_query=True,
        )
        roles = [entry.role for entry in result.entries]
        assert "retrieved_message" not in roles
        assert roles == ["record"]
        assert result.retrieved_messages is None

    def test_memory_query_keeps_retrieved_message_with_new_specificity(self):
        """Recall mode should keep raw hits that add distinct concrete detail."""
        result = build_context_with_ids(
            summaries=[],
            records=[
                ("o1", "User prefers Russian answers", "2026-03-10T10:00:00"),
            ],
            recent_messages=[],
            token_budget=10000,
            retrieved_messages=[
                (
                    "m1",
                    "assistant",
                    "Use the phrase Romance Standard Time in Windows timezone "
                    "settings.",
                    "c2",
                    "2026-03-10T09:00:00",
                ),
            ],
            search_results=[
                SearchResult(identifier="o1", source="keyword", score=0.05),
            ],
            memory_query=True,
        )
        roles = [entry.role for entry in result.entries]
        assert "retrieved_message" in roles

    def test_rrf_ordering_preserved(self):
        """Verify retrieved messages maintain input order (RRF score desc)."""
        result = build_context_with_ids(
            summaries=[],
            records=[],
            recent_messages=[],
            token_budget=10000,
            retrieved_messages=[
                ("m1", "user", "highest score", "c2", "2025-01-01T00:00:00"),
                ("m2", "user", "second score", "c2", "2025-01-02T00:00:00"),
                ("m3", "user", "third score", "c2", "2025-01-03T00:00:00"),
            ],
            session_id="c1",
        )
        contents = [e.content for e in result.entries]
        assert "highest score" in contents[0]
        assert "second score" in contents[1]
        assert "third score" in contents[2]


class TestSummaryRetrievalTier:
    """Tests for retrieved summary ranking and budgeting."""

    def test_summary_search_results_reorder_reflections(self):
        """Retrieved summaries should be labeled and placed first."""
        result = build_context_with_ids(
            summaries=[
                ("r1", "general preference", [], "2026-01-09T12:00:00"),
                (
                    "r2",
                    "prefers concise Russian answers",
                    ["o1"],
                    "2026-01-10T12:00:00",
                ),
            ],
            records=[],
            recent_messages=[],
            token_budget=10000,
            summary_search_results=[
                SearchResult(identifier="r2", source="keyword", score=0.05),
            ],
        )
        summaries = [entry for entry in result.entries if entry.role == "summary"]
        assert (
            summaries[0].content == "[Retrieved summary, 2026-01-10 12:00:00] "
            "prefers concise Russian answers"
        )
        assert result.retrieved_reflections == [
            {
                "main_point": "prefers concise Russian answers",
                "date": "2026-01-10 12:00:00",
            }
        ]

    def test_summary_search_results_penalize_orphaned_conflicted_summary(self):
        """Better-supported summaries should beat noisier matched summaries."""
        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "Kubernetes production preference",
                    [],
                    "2026-01-10T12:00:00",
                    "fresh",
                    3,
                    [
                        {"record_id": "o1", "relation": "conflicts"},
                    ],
                    "long_term",
                ),
                (
                    "r2",
                    "Kubernetes production preference",
                    ["o2", "o3"],
                    "2026-01-11T12:00:00",
                    "fresh",
                    3,
                    [
                        {"record_id": "o2", "relation": "supports"},
                        {"record_id": "o3", "relation": "supports"},
                    ],
                    "long_term",
                ),
            ],
            records=[],
            recent_messages=[],
            token_budget=10000,
            summary_search_results=[
                SearchResult(identifier="r1", source="keyword", score=0.80),
                SearchResult(identifier="r2", source="keyword", score=0.75),
            ],
        )
        summaries = [entry for entry in result.entries if entry.role == "summary"]
        assert (
            summaries[0].content == "[Retrieved summary, 2026-01-11 12:00:00] "
            "Kubernetes production preference"
        )

    def test_summary_ranking_prefers_linked_record_provenance(self):
        """Summary with deeper linked-record evidence should outrank thinner memory."""
        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "deployment preference",
                    ["o1"],
                    "2026-01-10T12:00:00",
                    "fresh",
                    2,
                    [{"record_id": "o1", "relation": "supports"}],
                    "long_term",
                ),
                (
                    "r2",
                    "deployment preference",
                    ["o2", "o3"],
                    "2026-01-11T12:00:00",
                    "fresh",
                    2,
                    [
                        {"record_id": "o2", "relation": "supports"},
                        {"record_id": "o3", "relation": "supports"},
                    ],
                    "long_term",
                ),
            ],
            records=[
                ("o1", "thin support", [], [], "2026-01-09T08:00:00", [], False),
                (
                    "o2",
                    "strong support one",
                    [],
                    [],
                    "2026-01-09T09:00:00",
                    ["m1", "m2"],
                    False,
                ),
                (
                    "o3",
                    "strong support two",
                    [],
                    [],
                    "2026-01-09T10:00:00",
                    ["m3"],
                    False,
                ),
            ],
            recent_messages=[],
            token_budget=10000,
            summary_search_results=[
                SearchResult(identifier="r1", source="keyword", score=0.091),
                SearchResult(identifier="r2", source="keyword", score=0.09),
            ],
        )
        summaries = [entry for entry in result.entries if entry.role == "summary"]
        assert (
            summaries[0].content
            == "[Retrieved summary, 2026-01-11 12:00:00] deployment preference"
        )

    def test_summary_budget_is_reserved_from_record_tier(self):
        """Long records should not fully starve summaries."""
        result = build_context_with_ids(
            summaries=[("r1", "r" * 400, "2026-01-10T12:00:00")],
            records=[
                ("o1", "x" * 400, "2026-01-01T12:00:00"),
                ("o2", "y" * 400, "2026-01-02T12:00:00"),
                ("o3", "z" * 400, "2026-01-03T12:00:00"),
            ],
            recent_messages=[],
            token_budget=320,
        )
        record_entries = [entry for entry in result.entries if entry.role == "record"]
        summary_entries = [entry for entry in result.entries if entry.role == "summary"]
        assert len(record_entries) == 2
        assert len(summary_entries) == 1

    def test_matched_summary_suppresses_redundant_observations(self):
        """Matched summary should keep only one covered matched record."""
        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "user prefers concise Russian answers",
                    ["o1", "o2"],
                    "2026-01-10T12:00:00",
                ),
            ],
            records=[
                ("o1", "asked for concise answers", "2026-01-09T12:00:00"),
                ("o2", "asked for Russian output", "2026-01-09T13:00:00"),
                ("o3", "wants markdown code samples", "2026-01-09T14:00:00"),
            ],
            recent_messages=[],
            token_budget=10000,
            search_results=[
                SearchResult(identifier="o1", source="keyword", score=0.05),
                SearchResult(identifier="o2", source="keyword", score=0.04),
                SearchResult(identifier="o3", source="keyword", score=0.03),
            ],
            summary_search_results=[
                SearchResult(identifier="r1", source="keyword", score=0.06),
            ],
        )
        record_entries = [
            entry.content for entry in result.entries if entry.role == "record"
        ]
        assert len(record_entries) == 2
        assert (
            "[Retrieved memory, 2026-01-09 12:00:00] asked for concise answers"
            in record_entries
        )
        assert (
            "[Retrieved memory, 2026-01-09 13:00:00] asked for Russian output"
            not in record_entries
        )

    def test_memory_query_excludes_outdated_reflections(self):
        """Memory-seeking queries should not include outdated summaries."""
        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "old preference",
                    ["o1"],
                    "2026-01-10T12:00:00",
                    "outdated",
                    1,
                    [{"record_id": "o1", "relation": "outdates"}],
                ),
                (
                    "r2",
                    "current preference",
                    ["o2"],
                    "2026-01-11T12:00:00",
                    "fresh",
                    3,
                    [{"record_id": "o2", "relation": "supports"}],
                ),
            ],
            records=[],
            recent_messages=[],
            token_budget=10000,
            memory_query=True,
            summary_search_results=[
                SearchResult(identifier="r1", source="keyword", score=0.06),
                SearchResult(identifier="r2", source="keyword", score=0.05),
            ],
        )
        summary_entries = [
            entry.content for entry in result.entries if entry.role == "summary"
        ]
        assert all("old preference" not in content for content in summary_entries)
        assert any("current preference" in content for content in summary_entries)

    def test_normal_chat_also_excludes_outdated_reflections(self):
        """Regular chat context should not carry outdated summaries either."""
        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "old preference",
                    ["o1"],
                    "2026-01-10T12:00:00",
                    "outdated",
                    1,
                    [{"record_id": "o1", "relation": "outdates"}],
                ),
                (
                    "r2",
                    "current preference",
                    ["o2"],
                    "2026-01-11T12:00:00",
                    "fresh",
                    3,
                    [{"record_id": "o2", "relation": "supports"}],
                ),
            ],
            records=[],
            recent_messages=[],
            token_budget=10000,
            memory_query=False,
        )
        summary_entries = [
            entry.content for entry in result.entries if entry.role == "summary"
        ]
        assert all("old preference" not in content for content in summary_entries)
        assert any("current preference" in content for content in summary_entries)

    def test_conflicting_relations_do_not_suppress_conflicting_observations(self):
        """Only supporting relations should suppress matched records."""
        result = build_context_with_ids(
            summaries=[
                (
                    "r1",
                    "mixed preference",
                    ["o1", "o2"],
                    "2026-01-10T12:00:00",
                    "mixed_support",
                    2,
                    [
                        {"record_id": "o1", "relation": "conflicts"},
                        {"record_id": "o2", "relation": "supports"},
                    ],
                ),
            ],
            records=[
                ("o1", "new conflicting preference", "2026-01-09T12:00:00"),
                ("o2", "still supported preference", "2026-01-09T13:00:00"),
            ],
            recent_messages=[],
            token_budget=10000,
            search_results=[
                SearchResult(identifier="o1", source="keyword", score=0.05),
                SearchResult(identifier="o2", source="keyword", score=0.04),
            ],
            summary_search_results=[
                SearchResult(identifier="r1", source="keyword", score=0.06),
            ],
        )
        record_entries = [
            entry.content for entry in result.entries if entry.role == "record"
        ]
        assert any(
            "new conflicting preference" in content for content in record_entries
        )


class TestSummaryDateLabels:
    """Tests for summary date labeling in build_context_with_ids."""

    def test_summary_tuple_with_date(self):
        """Summary tuple with created_at gets [YYYY-MM-DD] prefix."""
        result = build_context_with_ids(
            summaries=[("user likes Python", "2026-01-10T12:00:00.000")],
            records=[],
            recent_messages=[],
            token_budget=10000,
        )
        refs = [e for e in result.entries if e.role == "summary"]
        assert len(refs) == 1
        assert refs[0].content == "[2026-01-10 12:00:00] user likes Python"

    def test_summary_tuple_without_date(self):
        """Summary tuple with empty created_at has no prefix."""
        result = build_context_with_ids(
            summaries=[("user likes Python", "")],
            records=[],
            recent_messages=[],
            token_budget=10000,
        )
        refs = [e for e in result.entries if e.role == "summary"]
        assert len(refs) == 1
        assert refs[0].content == "user likes Python"

    def test_summary_plain_string_backward_compat(self):
        """Plain string summary works without date label."""
        result = build_context_with_ids(
            summaries=["user likes Python"],
            records=[],
            recent_messages=[],
            token_budget=10000,
        )
        refs = [e for e in result.entries if e.role == "summary"]
        assert len(refs) == 1
        assert refs[0].content == "user likes Python"


class TestTimezoneConversion:
    """Tests for UTC → user timezone conversion in context."""

    def test_record_converted_to_user_tz(self):
        """Record UTC timestamp converted to user timezone."""
        moscow = ZoneInfo("Europe/Moscow")
        result = build_context_with_ids(
            summaries=[],
            records=[("o1", "discussed async", "2026-03-10T11:30:00.000")],
            recent_messages=[],
            token_budget=10000,
            user_tz=moscow,
        )
        obs = [e for e in result.entries if e.role == "record"]
        # 11:30 UTC = 14:30 Moscow (+3)
        assert obs[0].content == "[2026-03-10 14:30:00] discussed async"

    def test_summary_converted_to_user_tz(self):
        """Summary UTC timestamp converted to user timezone."""
        moscow = ZoneInfo("Europe/Moscow")
        result = build_context_with_ids(
            summaries=[("user likes Python", "2026-01-10T09:00:00.000")],
            records=[],
            recent_messages=[],
            token_budget=10000,
            user_tz=moscow,
        )
        refs = [e for e in result.entries if e.role == "summary"]
        # 09:00 UTC = 12:00 Moscow (+3)
        assert refs[0].content == "[2026-01-10 12:00:00] user likes Python"

    def test_retrieved_message_converted_to_user_tz(self):
        """Retrieved message UTC timestamp converted to user timezone."""
        moscow = ZoneInfo("Europe/Moscow")
        result = build_context_with_ids(
            summaries=[],
            records=[],
            recent_messages=[],
            token_budget=10000,
            retrieved_messages=[
                ("m1", "user", "old detail", "c2", "2026-03-10T08:00:00.000"),
            ],
            session_id="c1",
            user_tz=moscow,
        )
        # 08:00 UTC = 11:00 Moscow (+3)
        assert (
            result.entries[0].content
            == "[Retrieved message, 2026-03-10 11:00:00] old detail"
        )

    def test_no_user_tz_keeps_utc(self):
        """Without user_tz, timestamps stay as UTC truncated."""
        result = build_context_with_ids(
            summaries=[],
            records=[("o1", "test", "2026-03-10T11:30:00.000")],
            recent_messages=[],
            token_budget=10000,
            user_tz=None,
        )
        obs = [e for e in result.entries if e.role == "record"]
        assert obs[0].content == "[2026-03-10 11:30:00] test"


class TestEstimateTokens:
    """Tests for token estimation."""

    def test_empty_string(self):
        """Verify empty string returns 0 tokens."""
        assert estimate_tokens("") == 0

    def test_short_string(self):
        """Verify short string token estimate."""
        assert estimate_tokens("hello world") == 2

    def test_long_string(self):
        """Verify long string token estimate."""
        text = "a" * 4000
        assert estimate_tokens(text) == 1000


class TestShouldCompress:
    """Tests for compression threshold detection."""

    def test_below_threshold(self):
        """Verify no compression below threshold."""
        messages = [("user", "short message")]
        assert not should_compress(messages, threshold=TEST_COMPRESSION_THRESHOLD)

    def test_above_threshold(self):
        """Verify compression above threshold."""
        messages = [("user", "x" * 20000)]
        assert should_compress(messages, threshold=TEST_COMPRESSION_THRESHOLD)

    def test_custom_threshold(self):
        """Verify custom threshold works."""
        messages = [("user", "x" * 100)]
        assert should_compress(messages, threshold=10)
        assert not should_compress(messages, threshold=100)
