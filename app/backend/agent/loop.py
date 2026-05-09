"""Agentic loop for the Gateway orchestrator.

Implements the LLM call -> tool extraction -> execution ->
feedback cycle with safety limits and stop handling. All safety
limits (iteration cap, timeout, injection-mode cap) come from
the assistant policy via AssistantPolicy.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from backend.security.policy import get_policy


logger = logging.getLogger(__name__)

# --- Catalog keys read from AssistantPolicy ---
_KEY_MAX_ITERATIONS = "agent_limits.iterations.max"
_KEY_INJECTION_MAX_ITERATIONS = "agent_limits.iterations.max_when_injection"
_KEY_LOOP_TIMEOUT_SECONDS = "agent_limits.loop_timeout_seconds"


class LoopState(StrEnum):
    """State of the agentic loop.

    RUNNING: Loop is actively processing.
    STOPPED: Loop was stopped by user.
    TIMEOUT: Loop exceeded time limit.
    ITERATION_LIMIT: Loop exceeded iteration limit.
    COMPLETE: Loop finished normally.
    """

    RUNNING = "running"
    STOPPED = "stopped"
    TIMEOUT = "timeout"
    ITERATION_LIMIT = "iteration_limit"
    COMPLETE = "complete"


@dataclass(frozen=True)
class LoopResult:
    """Result of an agentic loop execution.

    Attributes:
        state: Final state of the loop.
        iterations: Number of iterations completed.
        response: Final text response (if any).
    """

    state: LoopState
    iterations: int
    response: str


@dataclass
class AgenticLoop:
    """Controls the agentic loop lifecycle.

    Handles iteration counting, timeout enforcement,
    and stop button processing.

    Attributes:
        injection_flag: True if session has injection flag.
    """

    injection_flag: bool = False
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    _iteration: int = 0
    _start_time: float = 0.0
    _state: LoopState = LoopState.COMPLETE

    @property
    def max_iterations(self) -> int:
        """Return effective iteration limit."""
        return self._iteration_limit()

    @property
    def is_running(self) -> bool:
        """Return whether the loop is active."""
        return self._state == LoopState.RUNNING

    @property
    def iteration(self) -> int:
        """Return current iteration count."""
        return self._iteration

    def _iteration_limit(self) -> int:
        """Return the effective iteration cap from the active profile."""
        resolver = get_policy()
        if self.injection_flag:
            return resolver.get_int(_KEY_INJECTION_MAX_ITERATIONS)
        return resolver.get_int(_KEY_MAX_ITERATIONS)

    def _set_state(self, state: LoopState) -> LoopState:
        """Store one loop state and return it."""
        self._state = state
        return state

    def start(self) -> None:
        """Start a new agentic loop."""
        self._stop_event.clear()
        self._iteration = 0
        self._start_time = time.monotonic()
        self._state = LoopState.RUNNING

    def stop(self) -> None:
        """Signal the loop to stop."""
        self._stop_event.set()

    def check_limits(self) -> LoopState:
        """Check if any safety limit has been reached.

        Returns:
            Current LoopState after checking limits.
        """
        if self._stop_event.is_set():
            return self._set_state(LoopState.STOPPED)
        elapsed = time.monotonic() - self._start_time
        if elapsed > get_policy().get_int(_KEY_LOOP_TIMEOUT_SECONDS):
            return self._set_state(LoopState.TIMEOUT)
        if self._iteration >= self.max_iterations:
            return self._set_state(LoopState.ITERATION_LIMIT)
        return LoopState.RUNNING

    def advance(self) -> None:
        """Advance to the next iteration."""
        self._iteration += 1

    def complete(self, response: str) -> LoopResult:
        """Mark the loop as complete.

        Args:
            response: Final response text.

        Returns:
            LoopResult with final state.
        """
        if self._state == LoopState.RUNNING:
            self._state = LoopState.COMPLETE
        return LoopResult(
            state=self._state,
            iterations=self._iteration,
            response=response,
        )
