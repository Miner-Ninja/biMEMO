"""Per-tool rate limits within agentic loops.

Two layers of enforcement:
- ToolRateLimiter: per-loop counts, reset each agentic loop.
- PersistentRateLimiter: sliding-window limits that survive across loops.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


# Default per-tool limits (per agentic loop)
_DEFAULT_LIMITS: dict[str, int] = {
    "file_read": 10,
    "file_write": 5,
    "file_search": 5,
    "web_search": 10,
    "http_fetch": 2,
    "email_send": 3,
    "schedule_task": 5,
    "memory_recall": 5,
}

# Total budget across all tools per loop
_DEFAULT_BUDGET = 30
_INJECTION_BUDGET = 10

# Sliding-window limits: tool → (max_calls, window_seconds)
_WINDOW_LIMITS: dict[str, tuple[int, float]] = {
    "web_search": (10, 60.0),
}


def _tool_window_limits() -> dict[str, tuple[int, float]]:
    """Return the configured sliding-window limits for persistent tools."""
    return _WINDOW_LIMITS


@dataclass
class SlidingWindowLimiter:
    """Time-based sliding window limiter for a single tool.

    Attributes:
        max_calls: Maximum allowed calls within the window.
        window_seconds: Window duration in seconds.
    """

    max_calls: int
    window_seconds: float
    _timestamps: list[float] = field(default_factory=list)

    def _now(self) -> float:
        """Return the current monotonic timestamp."""
        return time.monotonic()

    def _active_timestamps(self) -> list[float]:
        """Return timestamps still inside the current sliding window."""
        cutoff = self._now() - self.window_seconds
        return [timestamp for timestamp in self._timestamps if timestamp > cutoff]

    def is_allowed(self) -> bool:
        """Check if another call is within the sliding window."""
        self._timestamps = self._active_timestamps()
        if len(self._timestamps) >= self.max_calls:
            logger.warning(
                "Sliding window limit reached: %d/%d calls in %.0fs",
                len(self._timestamps),
                self.max_calls,
                self.window_seconds,
            )
            return False
        return True

    def record(self) -> None:
        """Record a call timestamp."""
        self._timestamps.append(self._now())

    def remaining(self) -> int:
        """Return remaining calls in the current window."""
        active = self._active_timestamps()
        return max(0, self.max_calls - len(active))


@dataclass
class PersistentRateLimiter:
    """Sliding-window rate limiter that lives across agentic loops.

    Enforces time-based limits for tools where per-loop reset
    would be insufficient (e.g. web_search: 10 calls per 60s).

    Optionally persists call timestamps to the database so limits
    survive process restarts. Wire up record_fn and load_fn via
    initialize_from_db() after constructing.

    Attributes:
        record_fn: Async callable (tool_name, called_at) -> None.
        load_fn: Async callable (tool_name, since) -> list[float].
    """

    _windows: dict[str, SlidingWindowLimiter] = field(default_factory=dict)
    record_fn: Callable | None = field(default=None)
    load_fn: Callable | None = field(default=None)
    _record_tasks: set[asyncio.Task[object]] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Initialize sliding windows for tools with persistent limits."""
        for tool, (max_calls, window_sec) in _tool_window_limits().items():
            self._windows[tool] = SlidingWindowLimiter(max_calls, window_sec)

    def _window(self, tool_name: str) -> SlidingWindowLimiter | None:
        """Return the configured sliding window for one tool."""
        return self._windows.get(tool_name)

    async def initialize_from_db(self) -> None:
        """Load persisted call timestamps from the database.

        Should be called once at process start, after load_fn is set.
        """
        if self.load_fn is None:
            return
        now = time.time()
        for tool_name, (_, window_sec) in _tool_window_limits().items():
            since = now - window_sec
            try:
                result = await self.load_fn(tool_name, since)
                timestamps = result.get("timestamps", [])
                window = self._window(tool_name)
                if window is not None:
                    for ts in timestamps:
                        window._timestamps.append(ts)
            except Exception:
                logger.warning("Failed to load rate limit history for %s", tool_name)

    def is_allowed(self, tool_name: str) -> bool:
        """Check if a tool call is within the sliding window."""
        window = self._window(tool_name)
        if window is None:
            return True
        return window.is_allowed()

    def record(self, tool_name: str) -> None:
        """Record a tool call, persisting to DB if record_fn is set."""
        window = self._window(tool_name)
        if window is not None:
            window.record()
        if self.record_fn is not None:
            ts = time.time()
            with contextlib.suppress(RuntimeError):
                record_task = asyncio.ensure_future(self.record_fn(tool_name, ts))
                self._record_tasks.add(record_task)
                record_task.add_done_callback(self._record_tasks.discard)


@dataclass
class ToolRateLimiter:
    """Per-loop rate limiter for tool calls.

    Reset at the start of each agentic loop. Enforces per-tool
    call counts and total budget for a single loop execution.

    Attributes:
        injection_flag: True if session is injection-flagged.
    """

    injection_flag: bool = False
    _counts: dict[str, int] = field(default_factory=dict)
    _total: int = 0

    def _count(self, tool_name: str) -> int:
        """Return the current count for one tool."""
        return self._counts.get(tool_name, 0)

    def _get_limit(self, tool_name: str) -> int:
        """Get effective limit for a tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            Per-tool call limit.
        """
        base = _DEFAULT_LIMITS.get(tool_name, 1)
        if self.injection_flag:
            return max(1, base // 2)
        return base

    @property
    def budget(self) -> int:
        """Return total budget for this loop."""
        if self.injection_flag:
            return _INJECTION_BUDGET
        return _DEFAULT_BUDGET

    def is_allowed(self, tool_name: str) -> bool:
        """Check if a tool call is within limits.

        Args:
            tool_name: Name of the tool.

        Returns:
            True if within per-tool and total limits.
        """
        count = self._count(tool_name)
        if count >= self._get_limit(tool_name):
            logger.warning(
                "Per-loop limit reached: %s (%d)",
                tool_name,
                count,
            )
            return False
        if self._total >= self.budget:
            logger.warning(
                "Total budget exhausted: %d/%d",
                self._total,
                self.budget,
            )
            return False
        return True

    def record(self, tool_name: str) -> None:
        """Record a tool call.

        Args:
            tool_name: Name of the tool called.
        """
        self._counts[tool_name] = self._count(tool_name) + 1
        self._total += 1

    def reset_tool(self, tool_name: str) -> None:
        """Reset the limit for a specific tool.

        Called when user approves continuing after
        a limit was reached.

        Args:
            tool_name: Tool to reset.
        """
        if tool_name in self._counts:
            self._total -= self._counts[tool_name]
            del self._counts[tool_name]

    def get_count(self, tool_name: str) -> int:
        """Get current call count for a tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            Number of calls this loop.
        """
        return self._count(tool_name)
