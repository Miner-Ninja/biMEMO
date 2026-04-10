"""Tests for the central memory ranking layer."""

from backend.memory.memory_ranking import (
    MemoryRanker,
    RecordRankingInput,
    SummaryRankingInput,
)


def test_rank_summaries_prefers_stronger_evidence_when_relevance_matches() -> None:
    """Summaries with stronger evidence should outrank weaker peers."""
    ranker = MemoryRanker()
    candidates = [
        SummaryRankingInput(
            identifier="summary-weak",
            relevance_score=0.9,
            memory_layer="long_term",
            linked_record_count=1,
            source_message_count=0,
            has_full_provenance=False,
        ),
        SummaryRankingInput(
            identifier="summary-strong",
            relevance_score=0.9,
            memory_layer="long_term",
            linked_record_count=3,
            source_message_count=4,
            has_full_provenance=True,
        ),
    ]

    results = ranker.rank_summaries(candidates)

    assert results[0].identifier == "summary-strong"
    assert results[0].stronger_evidence_score > results[1].stronger_evidence_score


def test_rank_summaries_penalizes_stale_orphaned_duplicate_noise() -> None:
    """Noise-heavy summaries should sink below clean summaries."""
    ranker = MemoryRanker()
    candidates = [
        SummaryRankingInput(
            identifier="summary-clean",
            relevance_score=0.75,
            memory_layer="long_term",
            linked_record_count=2,
            source_message_count=2,
            has_full_provenance=True,
        ),
        SummaryRankingInput(
            identifier="summary-noisy",
            relevance_score=0.75,
            memory_layer="long_term",
            linked_record_count=2,
            source_message_count=2,
            has_full_provenance=False,
            is_stale=True,
            is_orphaned=True,
            is_duplicate=True,
            has_noise_markers=True,
        ),
    ]

    results = ranker.rank_summaries(candidates)

    assert results[0].identifier == "summary-clean"
    assert results[1].noise_penalty > results[0].noise_penalty


def test_rank_records_prefers_conversation_layer_and_provenance() -> None:
    """Conversation records with stronger evidence should outrank older weak records."""
    ranker = MemoryRanker()
    candidates = [
        RecordRankingInput(
            identifier="record-old",
            relevance_score=0.8,
            memory_layer="long_term",
            source_message_count=0,
            has_full_provenance=False,
            created_at="2025-01-10T10:00:00Z",
        ),
        RecordRankingInput(
            identifier="record-near",
            relevance_score=0.8,
            memory_layer="conversation",
            source_message_count=2,
            has_full_provenance=True,
            created_at="2026-03-24T10:00:00Z",
        ),
    ]

    results = ranker.rank_records(candidates)

    assert results[0].identifier == "record-near"
    assert results[0].memory_layer_score > results[1].memory_layer_score
    assert results[0].stronger_evidence_score > results[1].stronger_evidence_score


def test_rank_records_applies_tainted_and_conflict_penalties() -> None:
    """Tainted or conflicted records should lose score against clean records."""
    ranker = MemoryRanker()
    candidates = [
        RecordRankingInput(
            identifier="record-clean",
            relevance_score=0.7,
            memory_layer="session",
            source_message_count=1,
            has_full_provenance=True,
        ),
        RecordRankingInput(
            identifier="record-tainted",
            relevance_score=0.7,
            memory_layer="session",
            source_message_count=1,
            has_full_provenance=True,
            is_tainted=True,
            conflict_link_count=1,
            outdated_link_count=1,
        ),
    ]

    results = ranker.rank_records(candidates)

    assert results[0].identifier == "record-clean"
    assert results[1].noise_penalty > 0
    assert results[1].conflict_penalty > 0


def test_summary_ranking_result_contains_explainable_breakdown() -> None:
    """Summary ranking should expose a readable score breakdown."""
    ranker = MemoryRanker()
    result = ranker.rank_summaries(
        [
            SummaryRankingInput(
                identifier="summary-1",
                relevance_score=0.85,
                memory_layer="conversation",
                linked_record_count=2,
                source_message_count=3,
                has_full_provenance=True,
                trust_level_score=3,
                reuse_count=2,
                created_at="2026-03-24T10:00:00Z",
            )
        ]
    )[0]

    assert result.explanation.identifier == "summary-1"
    assert "relevance=" in result.explanation.details[0]
    assert any(
        detail.startswith("final_memory_score=")
        for detail in result.explanation.details
    )
