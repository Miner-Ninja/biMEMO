"""Tests for dynamic trust scoring.

Covers hash computation, score recording, score
retrieval, and downgrade suggestion.
"""

from backend.trust.scoring import (
    DOWNGRADE_THRESHOLD,
    TrustScorer,
)


class TestComputeHash:
    """Tests for parameter hash computation."""

    def test_deterministic(self):
        """Verify same inputs produce same hash."""
        h1 = TrustScorer.compute_hash("tool", "p", "v")
        h2 = TrustScorer.compute_hash("tool", "p", "v")
        assert h1 == h2

    def test_different_tools(self):
        """Verify different tools produce different hash."""
        h1 = TrustScorer.compute_hash("tool_a", "p", "v")
        h2 = TrustScorer.compute_hash("tool_b", "p", "v")
        assert h1 != h2

    def test_different_params(self):
        """Verify different params produce different hash."""
        h1 = TrustScorer.compute_hash("tool", "a", "v")
        h2 = TrustScorer.compute_hash("tool", "b", "v")
        assert h1 != h2

    def test_different_values(self):
        """Verify different values produce different hash."""
        h1 = TrustScorer.compute_hash("tool", "p", "v1")
        h2 = TrustScorer.compute_hash("tool", "p", "v2")
        assert h1 != h2

    def test_hash_is_hex(self):
        """Verify hash is hex-encoded SHA-256."""
        h = TrustScorer.compute_hash("tool", "p", "v")
        assert len(h) == 64
        int(h, 16)  # Valid hex


class TestRecordAndScore:
    """Tests for recording and retrieving scores."""

    def test_initial_score_zero(self):
        """Verify unseen combination returns 0."""
        scorer = TrustScorer()
        assert scorer.get_score("tool", "p", "v") == 0

    def test_record_increments(self):
        """Verify recording increments score."""
        scorer = TrustScorer()
        scorer.record_success("tool", "p", "v")
        assert scorer.get_score("tool", "p", "v") == 1

    def test_multiple_records(self):
        """Verify multiple records accumulate."""
        scorer = TrustScorer()
        for _ in range(5):
            scorer.record_success("tool", "p", "v")
        assert scorer.get_score("tool", "p", "v") == 5

    def test_separate_scores(self):
        """Verify different params have separate scores."""
        scorer = TrustScorer()
        scorer.record_success("tool", "p", "v1")
        scorer.record_success("tool", "p", "v1")
        scorer.record_success("tool", "p", "v2")
        assert scorer.get_score("tool", "p", "v1") == 2
        assert scorer.get_score("tool", "p", "v2") == 1

    def test_record_returns_count(self):
        """Verify record_success returns updated count."""
        scorer = TrustScorer()
        assert scorer.record_success("t", "p", "v") == 1
        assert scorer.record_success("t", "p", "v") == 2


class TestSuggestsDowngrade:
    """Tests for downgrade suggestion."""

    def test_below_threshold(self):
        """Verify no suggestion below threshold."""
        scorer = TrustScorer()
        scorer.record_success("tool", "p", "v")
        assert not scorer.suggests_downgrade("tool", "p", "v")

    def test_at_threshold(self):
        """Verify suggestion at threshold."""
        scorer = TrustScorer()
        for _ in range(DOWNGRADE_THRESHOLD):
            scorer.record_success("tool", "p", "v")
        assert scorer.suggests_downgrade("tool", "p", "v")

    def test_unseen_no_suggestion(self):
        """Verify no suggestion for unseen combination."""
        scorer = TrustScorer()
        assert not scorer.suggests_downgrade("tool", "p", "v")
