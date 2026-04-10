"""Memory event bus for live debug streaming.

Fan-out event bus: dialog recorder/summary distiller emit events, multiple
SSE subscribers receive them. Each subscriber gets its own
asyncio.Queue. Events are dropped on overflow (max 1000).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any


logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 1000
SubscriberQueue = asyncio.Queue[dict[str, Any]]


class MemoryEventBus:
    """Fan-out event bus for memory pipeline events.

    Attributes:
        _subscribers: Mapping of subscriber ID to their queue.
    """

    def __init__(self) -> None:
        """Initialize subscriber registry and queue counter."""
        self._subscribers: dict[str, SubscriberQueue] = {}
        self._next_id = 0

    def _next_subscriber_id(self) -> str:
        """Return the next subscriber identifier and advance the counter."""
        sub_id = str(self._next_id)
        self._next_id += 1
        return sub_id

    def _subscriber_queue(self) -> SubscriberQueue:
        """Create one bounded subscriber queue."""
        return asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

    def _subscriber_items(self) -> list[tuple[str, SubscriberQueue]]:
        """Return a stable snapshot of current subscriber queues."""
        return list(self._subscribers.items())

    def _event_with_timestamp(self, event: dict[str, Any]) -> dict[str, Any]:
        """Ensure every published event has a timestamp."""
        event.setdefault("timestamp", time.time())
        return event

    def subscribe(self) -> tuple[str, SubscriberQueue]:
        """Create a new subscriber queue.

        Returns:
            Tuple of (subscriber_id, queue).
        """
        sub_id = self._next_subscriber_id()
        queue = self._subscriber_queue()
        self._subscribers[sub_id] = queue
        logger.debug(
            "Event bus subscriber added: %s (total=%d)",
            sub_id,
            len(self._subscribers),
        )
        return sub_id, queue

    def unsubscribe(self, sub_id: str) -> None:
        """Remove a subscriber.

        Args:
            sub_id: Subscriber ID from subscribe().
        """
        self._subscribers.pop(sub_id, None)
        logger.debug(
            "Event bus subscriber removed: %s (total=%d)",
            sub_id,
            len(self._subscribers),
        )

    async def publish(self, event: dict[str, Any]) -> None:
        """Publish an event to all subscribers.

        Drops the event for any subscriber whose queue is full.

        Args:
            event: Event dict with at least a 'type' key.
        """
        event_with_timestamp = self._event_with_timestamp(event)
        for sub_id, queue in self._subscriber_items():
            try:
                queue.put_nowait(event_with_timestamp)
            except asyncio.QueueFull:
                logger.debug(
                    "Event bus queue full for subscriber %s, dropping event",
                    sub_id,
                )

    @property
    def subscriber_count(self) -> int:
        """Return number of active subscribers."""
        return len(self._subscribers)
