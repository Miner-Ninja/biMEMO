"""Central scoring logic for memory candidate ranking."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.memory.memory_ranking_models import (
    RankingExplanation,
    RankingWeights,
    RecordRankingInput,
    RecordRankingResult,
    SummaryRankingInput,
    SummaryRankingResult,
)


# --- Layer scoring ---
CONVERSATION_LAYER_SCORE = 3.0
SESSION_LAYER_SCORE = 2.0
LONG_TERM_LAYER_SCORE = 1.0
UNKNOWN_LAYER_SCORE = 0.0

# --- Evidence scoring ---
FULL_PROVENANCE_BONUS = 1.0
LINKED_NOTE_WEIGHT = 0.35
SOURCE_MESSAGE_WEIGHT = 0.2
MAX_LINKED_NOTE_SCORE = 1.4
MAX_SOURCE_MESSAGE_SCORE = 1.0

# --- Freshness scoring ---
FRESHNESS_LOOKBACK_DAYS = 30.0

# --- Stability scoring ---
TRUST_LEVEL_WEIGHT = 0.3
REUSE_COUNT_WEIGHT = 0.15
MAX_REUSE_SCORE = 0.9

# --- Penalty scoring ---
ORPHANED_PENALTY = 1.0
STALE_PENALTY = 0.8
DUPLICATE_PENALTY = 0.55
NOISE_MARKER_PENALTY = 0.5
TAINTED_NOTE_PENALTY = 0.7
MISSING_PROVENANCE_PENALTY = 0.8
CONFLICT_LINK_PENALTY = 0.45
OUTDATED_LINK_PENALTY = 0.35


class MemoryRanker:
    """Rank summaries and records by relevance, evidence strength, and noise."""

    def __init__(self, weights: RankingWeights | None = None) -> None:
        """Initialize one ranker with a stable weight profile."""
        self.weights = weights or RankingWeights()

    def rank_summaries(
        self,
        candidates: list[SummaryRankingInput],
    ) -> list[SummaryRankingResult]:
        """Rank summary candidates by final memory score."""
        results = [self._rank_summary(candidate) for candidate in candidates]
        return sorted(results, key=lambda item: item.final_memory_score, reverse=True)

    def rank_records(
        self,
        candidates: list[RecordRankingInput],
    ) -> list[RecordRankingResult]:
        """Rank record candidates by final memory score."""
        results = [self._rank_record(candidate) for candidate in candidates]
        return sorted(results, key=lambda item: item.final_memory_score, reverse=True)

    @staticmethod
    def _explanation_details(
        *,
        relevance_score: float,
        memory_layer: str,
        memory_layer_score: float,
        stronger_evidence_score: float,
        freshness_score: float,
        stability_score: float,
        noise_penalty: float,
        conflict_penalty: float,
        final_memory_score: float,
    ) -> list[str]:
        """Build the shared detailed score breakdown."""
        return [
            f"relevance={relevance_score:.2f}",
            f"layer={memory_layer} ({memory_layer_score:.2f})",
            f"stronger_evidence={stronger_evidence_score:.2f}",
            f"freshness={freshness_score:.2f}",
            f"stability={stability_score:.2f}",
            f"noise_penalty={noise_penalty:.2f}",
            f"conflict_penalty={conflict_penalty:.2f}",
            f"final_memory_score={final_memory_score:.2f}",
        ]

    def _ranking_breakdown(
        self,
        *,
        relevance_score: float,
        memory_layer: str,
        stronger_evidence_score: float,
        created_at: str,
        stability_score: float,
        noise_penalty: float,
        conflict_link_count: int,
        outdated_link_count: int,
    ) -> tuple[float, float, float, float, float]:
        """Build the shared breakdown used by summary and record ranking."""
        memory_layer_score = self._compute_memory_layer_score(memory_layer)
        freshness_score = self._compute_freshness_score(created_at)
        conflict_penalty = self._compute_conflict_penalty(
            conflict_link_count,
            outdated_link_count,
        )
        final_memory_score = self._compute_final_memory_score(
            relevance_score,
            memory_layer_score,
            stronger_evidence_score,
            freshness_score,
            stability_score,
            noise_penalty,
            conflict_penalty,
        )
        return (
            memory_layer_score,
            freshness_score,
            conflict_penalty,
            final_memory_score,
            noise_penalty,
        )

    def _rank_summary(self, candidate: SummaryRankingInput) -> SummaryRankingResult:
        """Score one summary candidate."""
        stronger_evidence_score = self._compute_summary_evidence_score(candidate)
        stability_score = self._compute_summary_stability_score(candidate)
        noise_penalty = self._compute_summary_noise_penalty(candidate)
        (
            memory_layer_score,
            freshness_score,
            conflict_penalty,
            final_memory_score,
            noise_penalty,
        ) = self._ranking_breakdown(
            relevance_score=candidate.relevance_score,
            memory_layer=candidate.memory_layer,
            stronger_evidence_score=stronger_evidence_score,
            created_at=candidate.created_at,
            stability_score=stability_score,
            noise_penalty=noise_penalty,
            conflict_link_count=candidate.conflict_link_count,
            outdated_link_count=candidate.outdated_link_count,
        )
        explanation = self._build_summary_explanation(
            candidate,
            memory_layer_score,
            stronger_evidence_score,
            freshness_score,
            stability_score,
            noise_penalty,
            conflict_penalty,
            final_memory_score,
        )
        return SummaryRankingResult(
            identifier=candidate.identifier,
            relevance_score=candidate.relevance_score,
            memory_layer_score=memory_layer_score,
            stronger_evidence_score=stronger_evidence_score,
            freshness_score=freshness_score,
            stability_score=stability_score,
            noise_penalty=noise_penalty,
            conflict_penalty=conflict_penalty,
            final_memory_score=final_memory_score,
            explanation=explanation,
        )

    def _rank_record(self, candidate: RecordRankingInput) -> RecordRankingResult:
        """Score one record candidate."""
        stronger_evidence_score = self._compute_record_evidence_score(candidate)
        stability_score = self._compute_record_stability_score(candidate)
        noise_penalty = self._compute_record_noise_penalty(candidate)
        (
            memory_layer_score,
            freshness_score,
            conflict_penalty,
            final_memory_score,
            noise_penalty,
        ) = self._ranking_breakdown(
            relevance_score=candidate.relevance_score,
            memory_layer=candidate.memory_layer,
            stronger_evidence_score=stronger_evidence_score,
            created_at=candidate.created_at,
            stability_score=stability_score,
            noise_penalty=noise_penalty,
            conflict_link_count=candidate.conflict_link_count,
            outdated_link_count=candidate.outdated_link_count,
        )
        explanation = self._build_record_explanation(
            candidate,
            memory_layer_score,
            stronger_evidence_score,
            freshness_score,
            stability_score,
            noise_penalty,
            conflict_penalty,
            final_memory_score,
        )
        return RecordRankingResult(
            identifier=candidate.identifier,
            relevance_score=candidate.relevance_score,
            memory_layer_score=memory_layer_score,
            stronger_evidence_score=stronger_evidence_score,
            freshness_score=freshness_score,
            stability_score=stability_score,
            noise_penalty=noise_penalty,
            conflict_penalty=conflict_penalty,
            final_memory_score=final_memory_score,
            explanation=explanation,
        )

    def _compute_memory_layer_score(self, memory_layer: str) -> float:
        """Return the fixed score for one memory layer label."""
        normalized_memory_layer = memory_layer.strip().casefold()
        if normalized_memory_layer == "conversation":
            return CONVERSATION_LAYER_SCORE
        if normalized_memory_layer == "session":
            return SESSION_LAYER_SCORE
        if normalized_memory_layer == "long_term":
            return LONG_TERM_LAYER_SCORE
        return UNKNOWN_LAYER_SCORE

    @staticmethod
    def _source_message_evidence_score(source_message_count: int) -> float:
        """Return the bounded score contribution from linked source messages."""
        return min(
            source_message_count * SOURCE_MESSAGE_WEIGHT,
            MAX_SOURCE_MESSAGE_SCORE,
        )

    @staticmethod
    def _provenance_bonus(has_full_provenance: bool) -> float:
        """Return the provenance bonus for one candidate."""
        return FULL_PROVENANCE_BONUS if has_full_provenance else 0.0

    @staticmethod
    def _missing_provenance_penalty(has_full_provenance: bool) -> float:
        """Return the missing-provenance penalty for one candidate."""
        return 0.0 if has_full_provenance else MISSING_PROVENANCE_PENALTY

    def _compute_summary_evidence_score(
        self,
        candidate: SummaryRankingInput,
    ) -> float:
        """Return the stronger-evidence score for one summary candidate."""
        linked_record_score = min(
            candidate.linked_record_count * LINKED_NOTE_WEIGHT,
            MAX_LINKED_NOTE_SCORE,
        )
        source_message_score = self._source_message_evidence_score(
            candidate.source_message_count
        )
        provenance_bonus = self._provenance_bonus(candidate.has_full_provenance)
        return linked_record_score + source_message_score + provenance_bonus

    def _compute_record_evidence_score(self, candidate: RecordRankingInput) -> float:
        """Return the stronger-evidence score for one record candidate."""
        source_message_score = self._source_message_evidence_score(
            candidate.source_message_count
        )
        provenance_bonus = self._provenance_bonus(candidate.has_full_provenance)
        return source_message_score + provenance_bonus

    def _compute_freshness_score(self, created_at: str) -> float:
        """Return a bounded freshness bonus based on age in days."""
        if not created_at.strip():
            return 0.0
        parsed_datetime = self._parse_iso_datetime(created_at)
        if parsed_datetime is None:
            return 0.0
        age_seconds = max(
            (datetime.now(UTC) - parsed_datetime).total_seconds(),
            0.0,
        )
        age_days = age_seconds / 86400.0
        if age_days >= FRESHNESS_LOOKBACK_DAYS:
            return 0.0
        return max(1.0 - (age_days / FRESHNESS_LOOKBACK_DAYS), 0.0)

    def _compute_summary_stability_score(
        self,
        candidate: SummaryRankingInput,
    ) -> float:
        """Return one summary stability score."""
        trust_score = max(float(candidate.trust_level_score), 0.0) * TRUST_LEVEL_WEIGHT
        reuse_score = min(candidate.reuse_count * REUSE_COUNT_WEIGHT, MAX_REUSE_SCORE)
        return trust_score + reuse_score

    def _compute_record_stability_score(self, candidate: RecordRankingInput) -> float:
        """Return one record stability score."""
        return min(candidate.reuse_count * REUSE_COUNT_WEIGHT, MAX_REUSE_SCORE)

    def _compute_summary_noise_penalty(
        self,
        candidate: SummaryRankingInput,
    ) -> float:
        """Return the accumulated noise penalty for one summary."""
        penalty = 0.0
        if candidate.is_orphaned:
            penalty += ORPHANED_PENALTY
        if candidate.is_stale:
            penalty += STALE_PENALTY
        if candidate.is_duplicate:
            penalty += DUPLICATE_PENALTY
        if candidate.has_noise_markers:
            penalty += NOISE_MARKER_PENALTY
        penalty += self._missing_provenance_penalty(candidate.has_full_provenance)
        return penalty

    def _compute_record_noise_penalty(self, candidate: RecordRankingInput) -> float:
        """Return the accumulated noise penalty for one record."""
        penalty = 0.0
        if candidate.is_tainted:
            penalty += TAINTED_NOTE_PENALTY
        if candidate.has_noise_markers:
            penalty += NOISE_MARKER_PENALTY
        penalty += self._missing_provenance_penalty(candidate.has_full_provenance)
        return penalty

    def _compute_conflict_penalty(
        self,
        conflict_link_count: int,
        outdated_link_count: int,
    ) -> float:
        """Return the accumulated conflict penalty for one candidate."""
        return (
            max(conflict_link_count, 0) * CONFLICT_LINK_PENALTY
            + max(outdated_link_count, 0) * OUTDATED_LINK_PENALTY
        )

    def _compute_final_memory_score(
        self,
        relevance_score: float,
        memory_layer_score: float,
        stronger_evidence_score: float,
        freshness_score: float,
        stability_score: float,
        noise_penalty: float,
        conflict_penalty: float,
    ) -> float:
        """Combine all score parts into one final ranking score."""
        return (
            relevance_score * self.weights.relevance_weight
            + memory_layer_score * self.weights.memory_layer_weight
            + stronger_evidence_score * self.weights.stronger_evidence_weight
            + freshness_score * self.weights.freshness_weight
            + stability_score * self.weights.stability_weight
            - noise_penalty * self.weights.noise_penalty_weight
            - conflict_penalty * self.weights.conflict_penalty_weight
        )

    def _build_summary_explanation(
        self,
        candidate: SummaryRankingInput,
        memory_layer_score: float,
        stronger_evidence_score: float,
        freshness_score: float,
        stability_score: float,
        noise_penalty: float,
        conflict_penalty: float,
        final_memory_score: float,
    ) -> RankingExplanation:
        """Build one readable explanation for summary ranking."""
        return self._ranking_explanation(
            item_kind="Summary",
            identifier=candidate.identifier,
            relevance_score=candidate.relevance_score,
            memory_layer=candidate.memory_layer,
            memory_layer_score=memory_layer_score,
            stronger_evidence_score=stronger_evidence_score,
            freshness_score=freshness_score,
            stability_score=stability_score,
            noise_penalty=noise_penalty,
            conflict_penalty=conflict_penalty,
            final_memory_score=final_memory_score,
        )

    def _build_record_explanation(
        self,
        candidate: RecordRankingInput,
        memory_layer_score: float,
        stronger_evidence_score: float,
        freshness_score: float,
        stability_score: float,
        noise_penalty: float,
        conflict_penalty: float,
        final_memory_score: float,
    ) -> RankingExplanation:
        """Build one readable explanation for record ranking."""
        return self._ranking_explanation(
            item_kind="Record",
            identifier=candidate.identifier,
            relevance_score=candidate.relevance_score,
            memory_layer=candidate.memory_layer,
            memory_layer_score=memory_layer_score,
            stronger_evidence_score=stronger_evidence_score,
            freshness_score=freshness_score,
            stability_score=stability_score,
            noise_penalty=noise_penalty,
            conflict_penalty=conflict_penalty,
            final_memory_score=final_memory_score,
        )

    def _ranking_explanation(
        self,
        *,
        item_kind: str,
        identifier: str,
        relevance_score: float,
        memory_layer: str,
        memory_layer_score: float,
        stronger_evidence_score: float,
        freshness_score: float,
        stability_score: float,
        noise_penalty: float,
        conflict_penalty: float,
        final_memory_score: float,
    ) -> RankingExplanation:
        """Build one readable explanation for either summary or record ranking."""
        details = self._explanation_details(
            relevance_score=relevance_score,
            memory_layer=memory_layer,
            memory_layer_score=memory_layer_score,
            stronger_evidence_score=stronger_evidence_score,
            freshness_score=freshness_score,
            stability_score=stability_score,
            noise_penalty=noise_penalty,
            conflict_penalty=conflict_penalty,
            final_memory_score=final_memory_score,
        )
        summary = (
            f"{item_kind} {identifier} ranked with relevance "
            f"{relevance_score:.2f} and stronger evidence "
            f"{stronger_evidence_score:.2f}."
        )
        return RankingExplanation(
            identifier=identifier,
            summary=summary,
            details=details,
        )

    def _parse_iso_datetime(self, raw_datetime: str) -> datetime | None:
        """Parse one ISO-like datetime into an aware UTC datetime."""
        normalized_datetime = raw_datetime.strip()
        if not normalized_datetime:
            return None
        if normalized_datetime.endswith("Z"):
            normalized_datetime = normalized_datetime[:-1] + "+00:00"
        try:
            parsed_datetime = datetime.fromisoformat(normalized_datetime)
        except ValueError:
            return None
        if parsed_datetime.tzinfo is None:
            return parsed_datetime.replace(tzinfo=UTC)
        return parsed_datetime.astimezone(UTC)
