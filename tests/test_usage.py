"""Tests for chat usage aggregation helpers."""

from backend.llm.usage import LLMCallUsage, aggregate_call_usages
from backend.models.chat_stats import ExtendedUsage


class TestAggregateCallUsages:
    """Tests for aggregating provider usage into response stats."""

    def test_falls_back_to_wall_time_when_provider_omits_timings(self):
        """Verify total wall time becomes generation time when none is provided."""
        stats = aggregate_call_usages(
            [
                LLMCallUsage(
                    prompt_tokens=321,
                    completion_tokens=54,
                    model="qwen3.5:35b-4bit",
                )
            ],
            fallback_model="qwen3.5:35b-4bit",
            total_wall_time_ms=2200.0,
        )

        assert stats is not None
        assert stats.timing.prompt_ms == 0.0
        assert stats.timing.generation_ms == 2200.0
        assert stats.timing.total_ms == 2200.0


class TestStageTimingBreakdown:
    """Tests for the per-phase timing breakdown (R1, R5, R6, R2)."""

    def _usage(self, *, prompt_ms: float, generation_ms: float) -> LLMCallUsage:
        return LLMCallUsage(
            prompt_tokens=10,
            completion_tokens=5,
            prompt_ms=prompt_ms,
            generation_ms=generation_ms,
            model="m",
        )

    def test_orders_phases_and_drops_zero_duration(self):
        """Phases keep their order, engine times append last, zeros drop (R5, R6)."""
        stats = aggregate_call_usages(
            [self._usage(prompt_ms=120.0, generation_ms=900.0)],
            fallback_model="m",
            total_wall_time_ms=1500.0,
            stage_phases=[
                ("Memory recall", 340.0),
                ("Context", 0.3),  # dropped: sub-millisecond, would read as "0ms"
                ("Web search", 1200.0),
            ],
        )

        assert stats is not None
        assert [(s.label, s.duration_ms) for s in stats.timing.stages] == [
            ("Memory recall", 340.0),
            ("Web search", 1200.0),
            ("Prompt", 120.0),
            ("Generation", 900.0),
        ]

    def test_sums_repeated_tool_labels_preserving_first_seen_order(self):
        """A tool called twice collapses to one summed line in first-seen order."""
        stats = aggregate_call_usages(
            [self._usage(prompt_ms=50.0, generation_ms=200.0)],
            fallback_model="m",
            total_wall_time_ms=600.0,
            stage_phases=[
                ("Web search", 300.0),
                ("File read", 40.0),
                ("Web search", 200.0),
            ],
        )

        assert stats is not None
        assert [(s.label, s.duration_ms) for s in stats.timing.stages] == [
            ("Web search", 500.0),
            ("File read", 40.0),
            ("Prompt", 50.0),
            ("Generation", 200.0),
        ]

    def test_no_stage_phases_yields_prompt_and_generation_only(self):
        """With no measured phases only non-zero engine times appear."""
        stats = aggregate_call_usages(
            [self._usage(prompt_ms=0.0, generation_ms=800.0)],
            fallback_model="m",
            total_wall_time_ms=800.0,
        )

        assert stats is not None
        assert [(s.label, s.duration_ms) for s in stats.timing.stages] == [
            ("Generation", 800.0),
        ]

    def test_fallback_generation_is_remainder_after_measured_phases(self):
        """Fallback: Generation is the remainder after measured phases (R1)."""
        stats = aggregate_call_usages(
            [self._usage(prompt_ms=0.0, generation_ms=0.0)],
            fallback_model="m",
            total_wall_time_ms=8000.0,
            stage_phases=[("Memory recall", 100.0), ("Web search", 3000.0)],
        )

        assert stats is not None
        assert stats.timing.generation_ms == 4900.0  # 8000 - (100 + 3000)
        assert [(s.label, s.duration_ms) for s in stats.timing.stages] == [
            ("Memory recall", 100.0),
            ("Web search", 3000.0),
            ("Generation", 4900.0),
        ]
        # The lines now partition the total turn time (no double-count).
        assert sum(s.duration_ms for s in stats.timing.stages) == 8000.0

    def test_stages_survive_model_dump_round_trip(self):
        """The breakdown persists through JSON serialisation and back (R2)."""
        stats = aggregate_call_usages(
            [self._usage(prompt_ms=120.0, generation_ms=900.0)],
            fallback_model="m",
            total_wall_time_ms=1200.0,
            stage_phases=[("Memory recall", 340.0)],
        )
        assert stats is not None

        reparsed = ExtendedUsage.model_validate(stats.model_dump(mode="json"))

        assert [(s.label, s.duration_ms) for s in reparsed.timing.stages] == [
            ("Memory recall", 340.0),
            ("Prompt", 120.0),
            ("Generation", 900.0),
        ]
