"""Tests for record rate limiting and queue management.

Covers hourly/daily rate limits, timestamp pruning,
bounded queue with drop policy, and async get.
"""

import asyncio
import time

from backend.memory.runtime.rate_limiter import (
    DialogRecorderQueue,
    DialogRecorderQueueItem,
    RecordRateLimiter,
)


class TestRecordRateLimiter:
    """Tests for rate limiting logic."""

    def test_allows_within_limits(self):
        """Verify records allowed within limits."""
        limiter = RecordRateLimiter(max_per_hour=5, max_per_day=10)
        for _ in range(5):
            assert limiter.is_allowed()
            limiter.record()

    def test_blocks_hourly_limit(self):
        """Verify blocks after hourly limit exceeded."""
        limiter = RecordRateLimiter(max_per_hour=2, max_per_day=100)
        limiter.record()
        limiter.record()
        assert not limiter.is_allowed()

    def test_blocks_daily_limit(self):
        """Verify blocks after daily limit exceeded."""
        limiter = RecordRateLimiter(max_per_hour=100, max_per_day=3)
        for _ in range(3):
            limiter.record()
        assert not limiter.is_allowed()

    def test_prunes_old_timestamps(self):
        """Verify old timestamps are pruned."""
        limiter = RecordRateLimiter(max_per_hour=1, max_per_day=100)
        # Record a timestamp "2 hours ago" (within day)
        old_time = time.monotonic() - 7200
        limiter._timestamps.append(old_time)
        # Should be allowed since hourly window reset
        assert limiter.is_allowed()

    def test_prune_removes_expired(self):
        """Verify prune removes timestamps older than 24h."""
        limiter = RecordRateLimiter(max_per_hour=100, max_per_day=1)
        # Add a timestamp 25 hours ago (beyond daily window)
        expired = time.monotonic() - 90000
        limiter._timestamps.append(expired)
        # Prune runs inside is_allowed; expired removed
        assert limiter.is_allowed()
        assert len(limiter._timestamps) == 0


class TestDialogRecorderQueue:
    """Tests for bounded dialog recorder queue."""

    def test_put_and_size(self):
        """Verify items are added and size tracks."""
        queue = DialogRecorderQueue(max_depth=5)
        queue.put(DialogRecorderQueueItem(messages=["msg1"]))
        queue.put(DialogRecorderQueueItem(messages=["msg2"]))
        assert queue.size == 2

    def test_drops_oldest_when_full(self):
        """Verify oldest item dropped when full."""
        queue = DialogRecorderQueue(max_depth=2)
        queue.put(DialogRecorderQueueItem(messages=["first"]))
        queue.put(DialogRecorderQueueItem(messages=["second"]))
        dropped = queue.put(DialogRecorderQueueItem(messages=["third"]))
        assert dropped
        assert queue.size == 2

    def test_no_drop_when_space(self):
        """Verify no drop when space available."""
        queue = DialogRecorderQueue(max_depth=10)
        dropped = queue.put(DialogRecorderQueueItem(messages=["msg"]))
        assert not dropped

    async def test_get_returns_item(self):
        """Verify get returns queued item."""
        queue = DialogRecorderQueue(max_depth=5)
        item = DialogRecorderQueueItem(messages=["hello"])
        queue.put(item)
        result = await queue.get()
        assert result.messages == ["hello"]
        assert queue.size == 0

    async def test_get_fifo_order(self):
        """Verify get returns items in FIFO order."""
        queue = DialogRecorderQueue(max_depth=5)
        queue.put(DialogRecorderQueueItem(messages=["first"]))
        queue.put(DialogRecorderQueueItem(messages=["second"]))
        first = await queue.get()
        second = await queue.get()
        assert first.messages == ["first"]
        assert second.messages == ["second"]

    async def test_get_waits_for_item(self):
        """Verify get blocks until item is available."""
        queue = DialogRecorderQueue(max_depth=5)

        async def delayed_put() -> None:
            await asyncio.sleep(0.05)
            queue.put(DialogRecorderQueueItem(messages=["delayed"]))

        task = asyncio.create_task(delayed_put())
        result = await queue.get()
        await task
        assert result.messages == ["delayed"]

    def test_put_coalesces_items_from_same_conversation(self):
        """Verify adjacent items from the same chat are merged."""
        queue = DialogRecorderQueue(max_depth=5)
        first = DialogRecorderQueueItem(
            messages=["User: first", "Assistant: first"],
            conversation_id="conv-1",
            session_id="sess-1",
            source_message_ids=["m1", "m2"],
        )
        second = DialogRecorderQueueItem(
            messages=["User: second", "Assistant: second"],
            conversation_id="conv-1",
            session_id="sess-1",
            source_message_ids=["m2", "m3"],
            injection_flagged=True,
        )

        first_dropped = queue.put(first)
        second_dropped = queue.put(second)

        assert not first_dropped
        assert not second_dropped
        assert queue.size == 1

        merged = queue._items[0]
        assert merged.messages == [
            "User: first",
            "Assistant: first",
            "User: second",
            "Assistant: second",
        ]
        assert merged.source_message_ids == ["m1", "m2", "m3"]
        assert merged.injection_flagged is True

    def test_put_stops_coalescing_after_item_limit(self):
        """Verify long chats split into multiple queue batches."""
        queue = DialogRecorderQueue(max_depth=5)
        queue.max_coalesced_items = 2

        for index in range(3):
            queue.put(
                DialogRecorderQueueItem(
                    messages=[f"dialog-step-{index}"],
                    conversation_id="conv-1",
                    session_id="sess-1",
                    source_message_ids=[f"m{index}"],
                )
            )

        assert queue.size == 2
        first_batch = queue._items[0]
        second_batch = queue._items[1]
        assert first_batch.messages == ["dialog-step-0", "dialog-step-1"]
        assert second_batch.messages == ["dialog-step-2"]

    def test_put_stops_coalescing_after_character_limit(self):
        """Verify very large batches do not merge into one oversized item."""
        queue = DialogRecorderQueue(max_depth=5)
        queue.max_coalesced_characters = 12

        first = DialogRecorderQueueItem(
            messages=["123456"],
            conversation_id="conv-1",
            session_id="sess-1",
        )
        second = DialogRecorderQueueItem(
            messages=["abcdef"],
            conversation_id="conv-1",
            session_id="sess-1",
        )
        third = DialogRecorderQueueItem(
            messages=["xyz"],
            conversation_id="conv-1",
            session_id="sess-1",
        )

        queue.put(first)
        queue.put(second)
        queue.put(third)

        assert queue.size == 2
        assert queue._items[0].messages == ["123456", "abcdef"]
        assert queue._items[1].messages == ["xyz"]

    def test_put_stops_coalescing_on_preference_change_signal(self):
        """Verify preference changes stay in their own dialog recorder batch."""
        queue = DialogRecorderQueue(max_depth=5)

        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: hello",
                    "Assistant: hello",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )
        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: now answer in more detailed steps",
                    "Assistant: understood, I will answer in more detail",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )

        assert queue.size == 2
        assert queue._items[0].messages == ["User: hello", "Assistant: hello"]
        assert queue._items[1].messages == [
            "User: now answer in more detailed steps",
            "Assistant: understood, I will answer in more detail",
        ]

    def test_put_stops_coalescing_on_new_task_signal(self):
        """Verify a new task does not merge into the previous casual dialog step."""
        queue = DialogRecorderQueue(max_depth=5)

        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: thanks",
                    "Assistant: you are welcome",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )
        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: next step is to prepare the deployment task",
                    "Assistant: I will prepare the deployment plan",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )

        assert queue.size == 2
        assert queue._items[0].messages == [
            "User: thanks",
            "Assistant: you are welcome",
        ]
        assert queue._items[1].messages == [
            "User: next step is to prepare the deployment task",
            "Assistant: I will prepare the deployment plan",
        ]

    def test_put_keeps_following_casual_dialog_step_away_from_boundary_batch(self):
        """Verify a boundary batch does not absorb the next casual dialog step."""
        queue = DialogRecorderQueue(max_depth=5)

        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: now answer in more detailed steps",
                    "Assistant: understood, I will answer in more detail",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )
        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: thanks",
                    "Assistant: okay",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )

        assert queue.size == 2
        assert queue._items[0].messages == [
            "User: now answer in more detailed steps",
            "Assistant: understood, I will answer in more detail",
        ]
        assert queue._items[1].messages == [
            "User: thanks",
            "Assistant: okay",
        ]

    def test_put_stops_coalescing_on_temporary_scope_signal(self):
        """Verify temporary-versus-durable dialog steps stay in separate batches."""
        queue = DialogRecorderQueue(max_depth=5)

        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: this is temporary short-term session context",
                    "Assistant: understood, this is temporary session context",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )
        queue.put(
            DialogRecorderQueueItem(
                messages=[
                    "User: keep my long-term preference separate from this task",
                    "Assistant: understood, I will keep durable preference separate",
                ],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )

        assert queue.size == 2
