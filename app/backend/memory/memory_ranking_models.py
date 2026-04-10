"""Data models for memory candidate ranking.

Defines the dataclasses used by the memory ranker to represent ranking
weights, candidate inputs, ranking results, and human-readable score
explanations. These models carry structured data through the ranking
pipeline without any scoring logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """Weights for the final memory ranking score."""

    relevance_weight: float = 3.0
    memory_layer_weight: float = 0.8
    stronger_evidence_weight: float = 1.3
    freshness_weight: float = 0.7
    stability_weight: float = 1.0
    noise_penalty_weight: float = 1.1
    conflict_penalty_weight: float = 1.0


@dataclass(frozen=True, slots=True)
class SummaryRankingInput:
    """Structured summary candidate input for ranking."""

    identifier: str
    relevance_score: float
    memory_layer: str
    linked_record_count: int = 0
    source_message_count: int = 0
    has_full_provenance: bool = False
    created_at: str = ""
    trust_level_score: int = 0
    reuse_count: int = 0
    is_stale: bool = False
    is_orphaned: bool = False
    is_duplicate: bool = False
    has_noise_markers: bool = False
    conflict_link_count: int = 0
    outdated_link_count: int = 0


@dataclass(frozen=True, slots=True)
class RecordRankingInput:
    """Structured record candidate input for ranking."""

    identifier: str
    relevance_score: float
    memory_layer: str
    source_message_count: int = 0
    has_full_provenance: bool = False
    created_at: str = ""
    reuse_count: int = 0
    is_tainted: bool = False
    has_noise_markers: bool = False
    conflict_link_count: int = 0
    outdated_link_count: int = 0


@dataclass(frozen=True, slots=True)
class RankingExplanation:
    """Human-readable explanation for one ranking decision."""

    identifier: str
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SummaryRankingResult:
    """Ranking output for one summary candidate."""

    identifier: str
    relevance_score: float
    memory_layer_score: float
    stronger_evidence_score: float
    freshness_score: float
    stability_score: float
    noise_penalty: float
    conflict_penalty: float
    final_memory_score: float
    explanation: RankingExplanation


@dataclass(frozen=True, slots=True)
class RecordRankingResult:
    """Ranking output for one record candidate."""

    identifier: str
    relevance_score: float
    memory_layer_score: float
    stronger_evidence_score: float
    freshness_score: float
    stability_score: float
    noise_penalty: float
    conflict_penalty: float
    final_memory_score: float
    explanation: RankingExplanation
