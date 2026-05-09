"""In-process message bus using asyncio for Core component communication.

Implements the MessageBus interface using simple async callback dispatch.
Used for intra-process communication between channel adapters, Gateway,
Tool Broker, and other Core components.
"""

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from backend.bus.message_bus import MessageBus, MessageHandler, MessagePayload


if TYPE_CHECKING:
    from collections.abc import Iterable


logger = logging.getLogger(__name__)


class AsyncQueueBus(MessageBus):
    """Message bus using async callbacks for intra-process pub/sub.

    Handlers are called sequentially for each published message.
    All communication stays within the same Python process.
    """

    def __init__(self) -> None:
        """Initialize with empty subscriber registry."""
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)

    def _topic_handlers(
        self,
        topic: str,
    ) -> list[MessageHandler]:
        """Return the handler list for one topic."""
        return self._handlers[topic]

    def _subscribed_handlers(self, topic: str) -> Iterable[MessageHandler]:
        """Return a stable snapshot of handlers for one topic."""
        return tuple(self._handlers.get(topic, ()))

    async def publish(self, topic: str, message: MessagePayload) -> None:
        """Publish a message to all subscribers of a topic.

        Args:
            topic: Topic name to publish to.
            message: Message payload.
        """
        for handler in self._subscribed_handlers(topic):
            await handler(message)

    async def subscribe(
        self,
        topic: str,
        handler: MessageHandler,
    ) -> None:
        """Register a handler for a topic.

        Args:
            topic: Topic name to subscribe to.
            handler: Async callable invoked for each message.
        """
        handlers = self._topic_handlers(topic)
        handlers.append(handler)
        logger.debug(
            "Subscribed handler to topic '%s' (total: %d)",
            topic,
            len(handlers),
        )
