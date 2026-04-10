"""Abstract message bus interface for publish/subscribe communication.

Defines the MessageBus protocol that decouples Core components from
the underlying transport mechanism. Concrete implementations include
AsyncQueueBus for intra-process and socket-based buses for IPC.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable


MessagePayload = object
MessageHandler = Callable[[MessagePayload], Awaitable[None]]


class MessageBus(ABC):
    """Abstract publish/subscribe message bus.

    Components publish messages to topics and subscribe handlers
    that are called when messages arrive on those topics.
    """

    @abstractmethod
    async def publish(self, topic: str, message: MessagePayload) -> None:
        """Publish a message to a topic.

        Args:
            topic: Topic name to publish to.
            message: Message payload (must be JSON-serializable
                for cross-process implementations).
        """

    @abstractmethod
    async def subscribe(
        self,
        topic: str,
        handler: MessageHandler,
    ) -> None:
        """Subscribe a handler to a topic.

        Args:
            topic: Topic name to subscribe to.
            handler: Async callable invoked for each message.
        """
