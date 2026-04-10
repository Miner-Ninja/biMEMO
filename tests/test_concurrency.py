"""Tests for cross-channel concurrency control.

Covers lock management, message queuing, and
dequeue behavior.
"""

from backend.agent.concurrency import (
    AgentConcurrency,
)


class TestLock:
    """Tests for concurrency lock."""

    async def test_not_busy_initially(self):
        """Verify not busy when no loop active."""
        gc = AgentConcurrency()
        assert not gc.is_busy

    async def test_busy_when_locked(self):
        """Verify busy when lock acquired."""
        gc = AgentConcurrency()
        await gc.acquire()
        assert gc.is_busy
        gc.release()

    async def test_release_unlocks(self):
        """Verify release makes not busy."""
        gc = AgentConcurrency()
        await gc.acquire()
        gc.release()
        assert not gc.is_busy


class TestQueue:
    """Tests for message queuing."""

    def test_enqueue_and_size(self):
        """Verify enqueue increases queue size."""
        gc = AgentConcurrency()
        gc.enqueue("telegram", "hello")
        assert gc.queue_size == 1

    def test_dequeue_returns_message(self):
        """Verify dequeue returns first message."""
        gc = AgentConcurrency()
        gc.enqueue("telegram", "first")
        gc.enqueue("web", "second")
        msg = gc.dequeue()
        assert msg is not None
        assert msg.channel == "telegram"
        assert msg.content == "first"
        assert gc.queue_size == 1

    def test_dequeue_empty_returns_none(self):
        """Verify dequeue on empty returns None."""
        gc = AgentConcurrency()
        assert gc.dequeue() is None

    def test_fifo_order(self):
        """Verify FIFO order."""
        gc = AgentConcurrency()
        gc.enqueue("a", "1")
        gc.enqueue("b", "2")
        gc.enqueue("c", "3")
        assert gc.dequeue().content == "1"
        assert gc.dequeue().content == "2"
        assert gc.dequeue().content == "3"
