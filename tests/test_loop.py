"""Tests for agentic loop execution.

Covers iteration counting, timeout detection, stop
handling, injection flag limits, and loop lifecycle.
"""

from unittest.mock import patch

from backend.agent.loop import (
    AgenticLoop,
    LoopState,
)


class TestLoopLifecycle:
    """Tests for loop start/stop/complete."""

    def test_starts_running(self):
        """Verify loop starts in running state."""
        loop = AgenticLoop()
        loop.start()
        assert loop.is_running
        assert loop.iteration == 0

    def test_stop_sets_stopped(self):
        """Verify stop signal sets stopped state."""
        loop = AgenticLoop()
        loop.start()
        loop.stop()
        assert loop.check_limits() == LoopState.STOPPED

    def test_complete_returns_result(self):
        """Verify complete returns result."""
        loop = AgenticLoop()
        loop.start()
        result = loop.complete("Done")
        assert result.state == LoopState.COMPLETE
        assert result.response == "Done"
        assert result.iterations == 0

    def test_advance_increments(self):
        """Verify advance increments iteration."""
        loop = AgenticLoop()
        loop.start()
        loop.advance()
        assert loop.iteration == 1


class TestIterationLimit:
    """Tests for iteration limit enforcement."""

    def test_default_limit_20(self):
        """Verify default max iterations is 20."""
        loop = AgenticLoop()
        assert loop.max_iterations == 20

    def test_blocks_at_limit(self):
        """Verify blocks at iteration limit."""
        loop = AgenticLoop()
        loop.start()
        for _ in range(20):
            loop.advance()
        state = loop.check_limits()
        assert state == LoopState.ITERATION_LIMIT

    def test_allows_within_limit(self):
        """Verify allows within limit."""
        loop = AgenticLoop()
        loop.start()
        loop.advance()
        assert loop.check_limits() == LoopState.RUNNING


class TestInjectionFlag:
    """Tests for injection-flagged limits."""

    def test_reduced_iterations(self):
        """Verify iterations reduced to 5."""
        loop = AgenticLoop(injection_flag=True)
        assert loop.max_iterations == 5

    def test_blocks_at_reduced_limit(self):
        """Verify blocks at 5 iterations."""
        loop = AgenticLoop(injection_flag=True)
        loop.start()
        for _ in range(5):
            loop.advance()
        state = loop.check_limits()
        assert state == LoopState.ITERATION_LIMIT


class TestTimeout:
    """Tests for timeout enforcement."""

    def test_timeout_detected(self):
        """Verify timeout is detected."""
        loop = AgenticLoop()
        loop.start()
        with patch(
            "backend.agent.loop.time.monotonic",
            return_value=301,
        ):
            loop._start_time = 0
            state = loop.check_limits()
        assert state == LoopState.TIMEOUT

    def test_complete_preserves_state(self):
        """Verify complete preserves stopped state."""
        loop = AgenticLoop()
        loop.start()
        loop.stop()
        loop.check_limits()
        result = loop.complete("partial")
        assert result.state == LoopState.STOPPED
