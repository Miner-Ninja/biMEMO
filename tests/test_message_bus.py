"""Tests for AsyncQueueBus publish/subscribe messaging.

Covers publish, subscribe, multiple subscribers, no subscribers,
and multiple topics.
"""

import pytest
from backend.bus.async_queue_bus import AsyncQueueBus


@pytest.fixture
def bus():
    """Create a fresh AsyncQueueBus instance."""
    return AsyncQueueBus()


class TestPublishSubscribe:
    """Tests for basic pub/sub behavior."""

    async def test_subscribe_and_publish(self, bus):
        """Verify subscriber receives published message."""
        received = []

        async def handler(msg):
            received.append(msg)

        await bus.subscribe("topic-a", handler)
        await bus.publish("topic-a", {"text": "hello"})
        assert len(received) == 1
        assert received[0] == {"text": "hello"}

    async def test_multiple_subscribers(self, bus):
        """Verify all subscribers receive the message."""
        received_a = []
        received_b = []

        async def handler_a(msg):
            received_a.append(msg)

        async def handler_b(msg):
            received_b.append(msg)

        await bus.subscribe("topic", handler_a)
        await bus.subscribe("topic", handler_b)
        await bus.publish("topic", "data")
        assert len(received_a) == 1
        assert len(received_b) == 1

    async def test_no_subscribers(self, bus):
        """Verify publish with no subscribers does not error."""
        await bus.publish("empty-topic", "data")

    async def test_topic_isolation(self, bus):
        """Verify messages go only to the subscribed topic."""
        received = []

        async def handler(msg):
            received.append(msg)

        await bus.subscribe("topic-x", handler)
        await bus.publish("topic-y", "wrong")
        await bus.publish("topic-x", "right")
        assert received == ["right"]
