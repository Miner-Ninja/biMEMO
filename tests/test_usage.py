"""Tests for chat usage aggregation helpers."""

from backend.llm.usage import LLMCallUsage, aggregate_call_usages


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
