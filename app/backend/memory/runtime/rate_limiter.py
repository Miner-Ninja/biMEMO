"""Rate limiting for record generation.

Enforces per-hour and per-day limits on records
to prevent resource exhaustion. Also provides a bounded
queue for Dialog Recorder processing.
"""

import asyncio
import logging
import time
from collections import deque

from backend.memory.runtime.rate_limiter_models import DialogRecorderQueueItem


logger = logging.getLogger(__name__)

# --- Default limits ---
MAX_PER_HOUR = 1000
MAX_PER_DAY = 24000
MAX_QUEUE_DEPTH = 50
MAX_COALESCED_DIALOG_RECORDER_ITEMS = 4
MAX_COALESCED_DIALOG_RECORDER_CHARACTERS = 6000
SEMANTIC_BOUNDARY_KEYWORDS = (
    "prefer",
    "preference",
    "style",
    "change",
    "changed",
    "instead",
    "now",
    "not anymore",
    "todo",
    "next step",
    "follow up",
    "need to",
    "must",
    "task",
    "action",
    "temporary",
    "durable",
    "short-term",
    "short term",
    "long-term",
    "long term",
    "session-specific",
    "session specific",
    "короче",
    "подроб",
    "предпоч",
    "стиль",
    "теперь",
    "вместо",
    "измен",
    "больше не",
    "сделать",
    "нужно",
    "надо",
    "дальше",
    "следующ",
    "задач",
    "временно",
    "временн",
    "долговрем",
    "краткосроч",
    "долгосроч",
)

HOUR_WINDOW_SECONDS = 3600
DAY_WINDOW_SECONDS = 86400


class RecordRateLimiter:
    """Rate limiter for record generation.

    Tracks record timestamps and enforces hourly
    and daily limits.

    Attributes:
        max_per_hour: Maximum records per hour.
        max_per_day: Maximum records per day.
    """

    def __init__(
        self,
        max_per_hour: int = MAX_PER_HOUR,
        max_per_day: int = MAX_PER_DAY,
    ) -> None:
        """Initialize the rate limiter.

        Args:
            max_per_hour: Hourly record limit.
            max_per_day: Daily record limit.
        """
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self._timestamps: deque[float] = deque()

    def is_allowed(self) -> bool:
        """Check if a new record is allowed.

        Returns:
            True if within rate limits.
        """
        now = time.monotonic()
        self._prune(now)
        hour_count, day_count = self._recent_counts(now)

        if hour_count >= self.max_per_hour:
            logger.warning("Record rate limit: hourly")
            return False
        if day_count >= self.max_per_day:
            logger.warning("Record rate limit: daily")
            return False
        return True

    def record(self) -> None:
        """Record a new record timestamp."""
        self._timestamps.append(time.monotonic())

    def _prune(self, now: float) -> None:
        """Remove timestamps older than 24 hours.

        Args:
            now: Current monotonic time.
        """
        cutoff = now - DAY_WINDOW_SECONDS
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def _window_count(self, *, now: float, window_seconds: int) -> int:
        """Count timestamps that still fall inside one sliding time window."""
        cutoff = now - window_seconds
        return sum(1 for timestamp in self._timestamps if timestamp > cutoff)

    def _recent_counts(self, now: float) -> tuple[int, int]:
        """Return the current record counts for the hourly and daily windows."""
        return (
            self._window_count(now=now, window_seconds=HOUR_WINDOW_SECONDS),
            self._window_count(now=now, window_seconds=DAY_WINDOW_SECONDS),
        )


def _merge_unique_strings(values: list[str]) -> list[str]:
    """Return input strings in order while removing duplicates."""
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def _contains_semantic_boundary_signal(item: DialogRecorderQueueItem) -> bool:
    """Return True when a queue item looks like a separate memory unit.

    The dialog recorder should still batch casual adjacent dialog steps, but a dialog
    step that
    clearly changes preference, introduces a task, or signals a change should
    be allowed to stand on its own more often.
    """
    combined_text = "\n".join(item.messages).lower()
    return any(keyword in combined_text for keyword in SEMANTIC_BOUNDARY_KEYWORDS)


def _can_coalesce_dialog_recorder_items(
    current: DialogRecorderQueueItem,
    new_item: DialogRecorderQueueItem,
    max_coalesced_items: int,
    max_coalesced_characters: int,
) -> bool:
    """Return True when two queued dialog recorder items can be merged safely."""
    if not (
        bool(current.conversation_id)
        and bool(current.session_id)
        and bool(new_item.conversation_id)
        and bool(new_item.session_id)
        and current.conversation_id == new_item.conversation_id
        and current.session_id == new_item.session_id
    ):
        return False

    coalesced_item_count = len(current.messages) + len(new_item.messages)
    if coalesced_item_count > max_coalesced_items:
        return False

    coalesced_character_count = sum(len(message) for message in current.messages) + sum(
        len(message) for message in new_item.messages
    )
    if coalesced_character_count > max_coalesced_characters:
        return False

    return not (
        _contains_semantic_boundary_signal(current)
        or _contains_semantic_boundary_signal(new_item)
    )


def _coalesce_dialog_recorder_items(
    current: DialogRecorderQueueItem,
    new_item: DialogRecorderQueueItem,
) -> DialogRecorderQueueItem:
    """Merge two dialog recorder queue items into one larger compression batch."""
    return DialogRecorderQueueItem(
        messages=current.messages + new_item.messages,
        conversation_id=current.conversation_id,
        session_id=current.session_id,
        source_message_ids=_merge_unique_strings(
            current.source_message_ids + new_item.source_message_ids
        ),
        injection_flagged=current.injection_flagged or new_item.injection_flagged,
    )


class DialogRecorderQueue:
    """Bounded async queue for Dialog Recorder processing.

    When the queue is full, oldest unprocessed items
    are dropped.

    Attributes:
        max_depth: Maximum queue depth.
    """

    def __init__(self, max_depth: int = MAX_QUEUE_DEPTH) -> None:
        """Initialize the queue.

        Args:
            max_depth: Maximum number of items in queue.
        """
        self.max_depth = max_depth
        self.max_coalesced_items = MAX_COALESCED_DIALOG_RECORDER_ITEMS
        self.max_coalesced_characters = MAX_COALESCED_DIALOG_RECORDER_CHARACTERS
        self._items: deque[DialogRecorderQueueItem] = deque(maxlen=max_depth)
        self._event = asyncio.Event()

    @property
    def size(self) -> int:
        """Return current queue size."""
        return len(self._items)

    def _mark_item_available(self) -> None:
        """Signal that the queue now contains at least one available item."""
        self._event.set()

    def _queue_would_drop_item(self) -> bool:
        """Return whether adding one more item would overflow the queue."""
        return len(self._items) >= self.max_depth

    def put(self, item: DialogRecorderQueueItem) -> bool:
        """Add an item to the queue.

        If the queue is at max depth, the oldest item
        is dropped to make room.

        Args:
            item: Queue item with messages and metadata.

        Returns:
            True if an item was dropped, False otherwise.
        """
        if self._items and _can_coalesce_dialog_recorder_items(
            self._items[-1],
            item,
            max_coalesced_items=self.max_coalesced_items,
            max_coalesced_characters=self.max_coalesced_characters,
        ):
            self._items[-1] = _coalesce_dialog_recorder_items(self._items[-1], item)
            self._mark_item_available()
            return False
        dropped = self._queue_would_drop_item()
        if dropped:
            logger.warning("Dialog Recorder queue full, dropping oldest")
        self._items.append(item)
        self._mark_item_available()
        return dropped

    def put_front(self, item: DialogRecorderQueueItem) -> bool:
        """Requeue an item at the front of the queue.

        If the queue is already full, the newest queued item is dropped
        so the interrupted item can be retried first.

        Args:
            item: Queue item with messages and metadata.

        Returns:
            True if an item was dropped, False otherwise.
        """
        dropped = self._queue_would_drop_item()
        if dropped:
            logger.warning("Dialog Recorder queue full, dropping newest queued item")
        self._items.appendleft(item)
        self._mark_item_available()
        return dropped

    async def get(self) -> DialogRecorderQueueItem:
        """Get the next item from the queue.

        Blocks until an item is available.

        Returns:
            Queue item with messages and metadata.
        """
        while not self._items:
            self._event.clear()
            await self._event.wait()
        return self._items.popleft()
