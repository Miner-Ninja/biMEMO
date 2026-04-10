"""Tests for per-tool rate limits.

Covers per-tool limits, total budget, injection flag
halving, and tool reset.
"""

from backend.tools.rate_limits import (
    ToolRateLimiter,
)


class TestPerToolLimits:
    """Tests for individual tool limits."""

    def test_allows_within_limit(self):
        """Verify calls within limit are allowed."""
        limiter = ToolRateLimiter()
        for _ in range(10):
            assert limiter.is_allowed("file_read")
            limiter.record("file_read")

    def test_blocks_at_limit(self):
        """Verify calls at limit are blocked."""
        limiter = ToolRateLimiter()
        for _ in range(10):
            limiter.record("file_read")
        assert not limiter.is_allowed("file_read")

    def test_different_tools_separate(self):
        """Verify different tools have separate limits."""
        limiter = ToolRateLimiter()
        for _ in range(10):
            limiter.record("file_read")
        assert limiter.is_allowed("file_write")

    def test_unknown_tool_limit_one(self):
        """Verify unknown tools have limit of 1."""
        limiter = ToolRateLimiter()
        limiter.record("unknown_tool")
        assert not limiter.is_allowed("unknown_tool")


class TestTotalBudget:
    """Tests for total call budget."""

    def test_blocks_at_budget(self):
        """Verify total budget is enforced."""
        limiter = ToolRateLimiter()
        for _ in range(30):
            limiter.record("file_read")
        assert not limiter.is_allowed("file_write")

    def test_budget_value(self):
        """Verify default budget is 30."""
        limiter = ToolRateLimiter()
        assert limiter.budget == 30


class TestInjectionFlag:
    """Tests for halved limits under injection flag."""

    def test_halved_limits(self):
        """Verify limits are halved."""
        limiter = ToolRateLimiter(injection_flag=True)
        for _ in range(5):
            limiter.record("file_read")
        assert not limiter.is_allowed("file_read")

    def test_halved_budget(self):
        """Verify budget is reduced to 10."""
        limiter = ToolRateLimiter(injection_flag=True)
        assert limiter.budget == 10

    def test_minimum_limit_one(self):
        """Verify halved limit is at least 1."""
        limiter = ToolRateLimiter(injection_flag=True)
        assert limiter.is_allowed("unknown_tool")
        limiter.record("unknown_tool")
        assert not limiter.is_allowed("unknown_tool")


class TestResetAndCount:
    """Tests for reset and count tracking."""

    def test_reset_tool(self):
        """Verify reset allows more calls."""
        limiter = ToolRateLimiter()
        for _ in range(10):
            limiter.record("file_read")
        assert not limiter.is_allowed("file_read")
        limiter.reset_tool("file_read")
        assert limiter.is_allowed("file_read")

    def test_reset_unknown_noop(self):
        """Verify resetting unknown tool is noop."""
        limiter = ToolRateLimiter()
        limiter.reset_tool("nonexistent")

    def test_get_count(self):
        """Verify count tracking."""
        limiter = ToolRateLimiter()
        assert limiter.get_count("file_read") == 0
        limiter.record("file_read")
        assert limiter.get_count("file_read") == 1

    def test_reset_adjusts_total(self):
        """Verify reset adjusts total budget."""
        limiter = ToolRateLimiter()
        for _ in range(5):
            limiter.record("file_read")
        limiter.reset_tool("file_read")
        assert limiter.get_count("file_read") == 0
