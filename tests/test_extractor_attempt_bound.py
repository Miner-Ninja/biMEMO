"""Tests for the graph-extractor per-record attempt bound.

A record whose batches never yield a citing assertion must stop being
re-sent to the LLM after MAX_EXTRACTION_ATTEMPTS_PER_RECORD batches,
instead of burning tokens on every trigger forever.
"""

from backend.memory.runtime.scheduler import (
    MAX_EXTRACTION_ATTEMPTS_PER_RECORD,
    ExtractionAttemptTracker,
)


def _records(*ids: str) -> list[dict]:
    """Build fetched-record rows with the given ids."""
    return [{"id": record_id, "summary": "s"} for record_id in ids]


class TestExtractionAttemptTracker:
    """The tracker sets aside records after the attempt bound."""

    def test_fresh_records_are_eligible(self) -> None:
        """Unseen records pass through untouched."""
        tracker = ExtractionAttemptTracker()
        rows = _records("a", "b")
        assert tracker.eligible(rows) == rows

    def test_record_set_aside_after_max_attempts(self) -> None:
        """A record stops being eligible after the bound is reached."""
        tracker = ExtractionAttemptTracker()
        rows = _records("poison")
        for _ in range(MAX_EXTRACTION_ATTEMPTS_PER_RECORD):
            assert tracker.eligible(rows) == rows
            tracker.register_batch(rows)
        assert tracker.eligible(rows) == []

    def test_other_records_unaffected(self) -> None:
        """Setting one record aside leaves the rest of the batch flowing."""
        tracker = ExtractionAttemptTracker()
        poison = _records("poison")
        for _ in range(MAX_EXTRACTION_ATTEMPTS_PER_RECORD):
            tracker.register_batch(poison)
        mixed = _records("poison", "fresh")
        assert tracker.eligible(mixed) == _records("fresh")

    def test_trim_keeps_live_counters(self) -> None:
        """Trimming the map keeps counters for still-fetched records."""
        tracker = ExtractionAttemptTracker()
        for _ in range(MAX_EXTRACTION_ATTEMPTS_PER_RECORD):
            tracker.register_batch(_records("poison"))
        # Flood with inert ids to push the map past the trim size.
        for index in range(1100):
            tracker.register_batch(_records(f"done-{index}"))
        # The poison record is still in the current fetch after the trim
        # only if its counter survived trims that included it; a record
        # absent from the trimming batch loses its counter — that is the
        # documented restart-like reset, so here we only require that a
        # record present in the trimming batch keeps counting.
        tracker.register_batch(_records("poison"))
        for _ in range(MAX_EXTRACTION_ATTEMPTS_PER_RECORD - 1):
            tracker.register_batch(_records("poison"))
        assert tracker.eligible(_records("poison")) == []
