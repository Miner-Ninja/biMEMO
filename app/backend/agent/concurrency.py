"""Cross-channel concurrency control for the Gateway.

Serializes agentic loop execution to prevent concurrent
modifications. Queues messages when a loop is active.
"""

import asyncio
import logging
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedMessage:
    """A message waiting to be processed.

    Attributes:
        channel: Origin channel (telegram, web).
        content: Message text.
    """

    channel: str
    content: str


@dataclass
class AgentConcurrency:
    """Serializes agentic loop execution.

    Only one agentic loop runs at a time. Messages
    arriving during execution are queued.
    """

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _queue: list[QueuedMessage] = field(default_factory=list)

    def _messages(self) -> list[QueuedMessage]:
        """Return the mutable queued-message list."""
        return self._queue

    def _has_queued_messages(self) -> bool:
        """Return whether any queued messages are waiting."""
        return bool(self._messages())

    @property
    def is_busy(self) -> bool:
        """Return whether a loop is currently active."""
        return self._lock.locked()

    @property
    def queue_size(self) -> int:
        """Return number of queued messages."""
        return len(self._messages())

    def enqueue(self, channel: str, content: str) -> None:
        """Queue a message for later processing.

        Args:
            channel: Origin channel.
            content: Message text.
        """
        messages = self._messages()
        messages.append(QueuedMessage(channel=channel, content=content))
        logger.info(
            "Message queued from %s (queue: %d)",
            channel,
            len(messages),
        )

    def dequeue(self) -> QueuedMessage | None:
        """Get the next queued message.

        Returns:
            QueuedMessage if available, None otherwise.
        """
        messages = self._messages()
        if self._has_queued_messages():
            return messages.pop(0)
        return None

    async def acquire(self) -> None:
        """Acquire the loop execution lock."""
        await self._lock.acquire()

    def release(self) -> None:
        """Release the loop execution lock."""
        self._lock.release()
