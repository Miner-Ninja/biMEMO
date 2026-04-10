"""Tests for ContextZoneBudget and estimate_tokens.

Covers token estimation edge cases, zone budget computation for
various context window sizes, ratio validation, and boundary
conditions like zero context or large system prompts.
"""

from backend.memory.context_builder.constants import (
    CONVERSATION_SUMMARY_ZONE_RATIO,
    RECALL_RESULTS_ZONE_RATIO,
    WORKING_MEMORY_ZONE_RATIO,
    estimate_tokens,
)
from backend.memory.context_builder.models import ContextZoneBudget


class TestEstimateTokens:
    """Tests for the estimate_tokens helper."""

    def test_estimate_tokens_short_text(self) -> None:
        """Short text returns a reasonable estimate based on char length."""
        text = "Hello world"
        result = estimate_tokens(text)
        # 11 chars // 4 = 2
        assert result == 2

    def test_estimate_tokens_empty(self) -> None:
        """Empty string returns 1 as the minimum token count."""
        assert estimate_tokens("") == 1


class TestContextZoneBudget:
    """Tests for ContextZoneBudget.from_context_window computation."""

    def test_zone_budget_16k_model(self) -> None:
        """16K context window produces correct zone ordering and total."""
        budget = ContextZoneBudget.from_context_window(num_ctx=16384)
        assert budget.working_memory > budget.recall_results
        assert budget.recall_results > budget.conversation_summary
        total_zones = (
            budget.working_memory + budget.recall_results + budget.conversation_summary
        )
        assert total_zones <= budget.total_available

    def test_zone_budget_128k_model(self) -> None:
        """128K context window scales zones proportionally to 16K."""
        budget_16k = ContextZoneBudget.from_context_window(num_ctx=16384)
        budget_128k = ContextZoneBudget.from_context_window(num_ctx=131072)

        scale = budget_128k.total_available / budget_16k.total_available
        # Scale should be roughly 8x (128k / 16k), allow some tolerance
        assert 7.0 < scale < 9.0

        # Each zone should scale by about the same factor
        wm_scale = budget_128k.working_memory / budget_16k.working_memory
        recall_scale = budget_128k.recall_results / budget_16k.recall_results
        summary_scale = (
            budget_128k.conversation_summary / budget_16k.conversation_summary
        )
        assert abs(wm_scale - recall_scale) < 0.5
        assert abs(recall_scale - summary_scale) < 0.5

    def test_zone_budget_with_large_system_prompt(self) -> None:
        """Large system prompt reduces available tokens and shrinks zones."""
        budget_no_prompt = ContextZoneBudget.from_context_window(num_ctx=16384)
        large_prompt = "x" * 8000  # ~2000 tokens
        budget_with_prompt = ContextZoneBudget.from_context_window(
            num_ctx=16384,
            system_prompt=large_prompt,
        )

        assert budget_with_prompt.total_available < budget_no_prompt.total_available
        assert budget_with_prompt.working_memory < budget_no_prompt.working_memory
        assert budget_with_prompt.recall_results < budget_no_prompt.recall_results

    def test_zone_budget_ratios_sum_to_100_percent(self) -> None:
        """Working, recall, and summary zone ratios sum to 1.0."""
        total = (
            WORKING_MEMORY_ZONE_RATIO
            + RECALL_RESULTS_ZONE_RATIO
            + CONVERSATION_SUMMARY_ZONE_RATIO
        )
        assert total == 1.0

    def test_zone_budget_zero_context_raises(self) -> None:
        """Zero context window is rejected: silent fallback removed."""
        import pytest

        with pytest.raises(ValueError, match="positive integer"):
            ContextZoneBudget.from_context_window(num_ctx=0)

    def test_zone_budget_negative_context_raises(self) -> None:
        """Negative context window is rejected."""
        import pytest

        with pytest.raises(ValueError, match="positive integer"):
            ContextZoneBudget.from_context_window(num_ctx=-1)

    def test_zone_budget_response_reserve_minimum(self) -> None:
        """Response reserve is at least 512 tokens even for small windows."""
        budget = ContextZoneBudget.from_context_window(num_ctx=1024)
        assert budget.response_reserve >= 512
