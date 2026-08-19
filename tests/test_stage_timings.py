"""Tests for the per-turn stage-timing accumulator."""

from backend.agent.stage_timings import StageTimings, humanize_stage_label


class TestHumanizeStageLabel:
    """Tests for turning raw tool names into display labels."""

    def test_underscores_become_spaces_and_first_letter_capitalised(self):
        """Underscores become spaces and the first letter is capitalised."""
        assert humanize_stage_label("web_search") == "Web search"
        assert humanize_stage_label("memory_recall") == "Memory recall"

    def test_single_word_is_capitalised(self):
        """A single-word tool name is simply capitalised."""
        assert humanize_stage_label("fetch") == "Fetch"


class TestStageTimings:
    """Tests for recording ordered phase spans."""

    def test_records_phases_in_insertion_order(self):
        """Recorded phases are returned in the order they were recorded."""
        timings = StageTimings()
        timings.record("Memory recall", 340.0)
        timings.record("Web search", 1200.0)

        assert timings.phases == [("Memory recall", 340.0), ("Web search", 1200.0)]

    def test_phases_returns_a_copy(self):
        """The phases property returns a copy, not the internal list."""
        timings = StageTimings()
        timings.record("Context", 12.0)

        snapshot = timings.phases
        snapshot.append(("tampered", 0.0))

        assert timings.phases == [("Context", 12.0)]
