"""Tests for Dialog Recorder/Summary Distiller scheduling modes.

Covers idle mode (waits for primary), parallel mode
(runs immediately), signal transitions, and background
task loops.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from backend.memory.runtime.rate_limiter import (
    DialogRecorderQueue,
    DialogRecorderQueueItem,
)
from backend.memory.runtime.scheduler import (
    DialogRecorderScheduler,
    SchedulingMode,
    _count_source_message_coverage,
    _should_run_extraction,
    run_dialog_recorder_loop,
    run_graph_extractor_loop,
)


class TestSchedulingMode:
    """Tests for SchedulingMode enum."""

    def test_idle_value(self):
        """Verify idle mode string value."""
        assert SchedulingMode.IDLE == "idle"

    def test_parallel_value(self):
        """Verify parallel mode string value."""
        assert SchedulingMode.PARALLEL == "parallel"


class TestParallelMode:
    """Tests for parallel mode scheduling."""

    async def test_parallel_returns_immediately(self):
        """Verify parallel mode does not block."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        await asyncio.wait_for(scheduler.wait_for_primary(), timeout=0.1)

    async def test_signal_start_noop_in_parallel(self):
        """Verify signal_primary_start is noop in parallel."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        scheduler.signal_primary_start()
        await asyncio.wait_for(scheduler.wait_for_primary(), timeout=0.1)


class TestIdleMode:
    """Tests for idle mode scheduling."""

    async def test_idle_blocks_until_done(self):
        """Verify idle mode blocks until primary done."""
        scheduler = DialogRecorderScheduler(SchedulingMode.IDLE)
        scheduler.signal_primary_start()

        async def signal_later() -> None:
            await asyncio.sleep(0.05)
            scheduler.signal_primary_done()

        task = asyncio.create_task(signal_later())
        await asyncio.wait_for(scheduler.wait_for_primary(), timeout=1.0)
        await task

    async def test_idle_start_clears_event(self):
        """Verify signal_primary_start clears the event."""
        scheduler = DialogRecorderScheduler(SchedulingMode.IDLE)
        scheduler.signal_primary_done()
        scheduler.signal_primary_start()

        with_timeout = asyncio.wait_for(scheduler.wait_for_primary(), timeout=0.05)
        try:
            await with_timeout
            completed = True
        except TimeoutError:
            completed = False
        assert not completed

    async def test_signal_done_unblocks(self):
        """Verify signal_primary_done unblocks wait."""
        scheduler = DialogRecorderScheduler(SchedulingMode.IDLE)
        scheduler.signal_primary_done()
        await asyncio.wait_for(scheduler.wait_for_primary(), timeout=0.1)

    async def test_idle_grace_period_delays_background_work(self):
        """Verify idle mode waits for the configured grace period."""
        scheduler = DialogRecorderScheduler(
            SchedulingMode.IDLE,
            idle_grace_period_seconds=0.05,
        )
        scheduler.signal_primary_done()

        with_timeout = asyncio.wait_for(scheduler.wait_for_primary(), timeout=0.01)
        try:
            await with_timeout
            completed_too_early = True
        except TimeoutError:
            completed_too_early = False

        assert not completed_too_early
        await asyncio.wait_for(scheduler.wait_for_primary(), timeout=0.1)

    async def test_primary_restart_during_grace_keeps_scheduler_blocked(self):
        """Verify new primary activity cancels a pending grace window."""
        scheduler = DialogRecorderScheduler(
            SchedulingMode.IDLE,
            idle_grace_period_seconds=0.05,
        )
        scheduler.signal_primary_done()

        async def restart_primary() -> None:
            await asyncio.sleep(0.01)
            scheduler.signal_primary_start()

        restart_task = asyncio.create_task(restart_primary())

        with_timeout = asyncio.wait_for(scheduler.wait_for_primary(), timeout=0.08)
        try:
            await with_timeout
            completed = True
        except TimeoutError:
            completed = False

        await restart_task
        assert not completed

    async def test_background_slot_serializes_memory_tasks_in_idle_mode(self):
        """Verify idle mode still serializes background tasks."""
        scheduler = DialogRecorderScheduler(SchedulingMode.IDLE)
        await scheduler.acquire_background_slot()

        acquired_event = asyncio.Event()

        async def wait_for_slot() -> None:
            await scheduler.acquire_background_slot()
            acquired_event.set()

        waiter_task = asyncio.create_task(wait_for_slot())
        await asyncio.sleep(0.05)
        assert not acquired_event.is_set()

        scheduler.release_background_slot()
        await asyncio.wait_for(acquired_event.wait(), timeout=0.1)

        scheduler.release_background_slot()
        await waiter_task

    async def test_parallel_mode_does_not_serialize_background_tasks(self):
        """Verify parallel mode no longer blocks dialog recorder and distiller."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)

        await scheduler.acquire_background_slot()

        acquired_event = asyncio.Event()

        async def wait_for_slot() -> None:
            await scheduler.acquire_background_slot()
            acquired_event.set()

        waiter_task = asyncio.create_task(wait_for_slot())
        await asyncio.wait_for(acquired_event.wait(), timeout=0.1)
        scheduler.release_background_slot()
        await waiter_task


class TestRunDialogRecorderLoop:
    """Tests for run_dialog_recorder_loop background task."""

    @patch(
        "backend.memory.runtime.scheduler.process_record",
        new_callable=AsyncMock,
        return_value="obs-1",
    )
    async def test_processes_one_item(self, mock_proc):
        """Verify loop processes an item from queue."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        queue = DialogRecorderQueue(max_depth=5)
        queue.put(
            DialogRecorderQueueItem(
                messages=["hello"],
                conversation_id="conv-1",
                session_id="sess-1",
            )
        )
        provider = MagicMock()
        db_client = MagicMock()
        limiter = MagicMock()

        task = asyncio.create_task(
            run_dialog_recorder_loop(queue, provider, db_client, limiter, scheduler)
        )
        # Allow the loop to process the item
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        mock_proc.assert_awaited_once_with(
            provider=provider,
            db_client=db_client,
            messages=["hello"],
            conversation_id="conv-1",
            session_id="sess-1",
            source_message_ids=[],
            rate_limiter=limiter,
            injection_flagged=False,
            embed_fn=None,
            event_bus=None,
        )

    @patch(
        "backend.memory.runtime.scheduler.process_record",
        new_callable=AsyncMock,
    )
    async def test_respects_scheduler_wait(self, mock_proc):
        """Verify loop waits for scheduler in idle mode."""
        scheduler = DialogRecorderScheduler(SchedulingMode.IDLE)
        scheduler.signal_primary_start()
        queue = DialogRecorderQueue(max_depth=5)
        queue.put(DialogRecorderQueueItem(messages=["waiting"]))
        provider = MagicMock()
        db_client = MagicMock()
        limiter = MagicMock()

        task = asyncio.create_task(
            run_dialog_recorder_loop(queue, provider, db_client, limiter, scheduler)
        )
        await asyncio.sleep(0.05)
        # Should not have processed yet (blocked by scheduler)
        mock_proc.assert_not_awaited()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_handles_cancelled_error(self):
        """Verify loop exits gracefully on cancellation."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        queue = DialogRecorderQueue(max_depth=5)
        provider = MagicMock()
        db_client = MagicMock()
        limiter = MagicMock()

        task = asyncio.create_task(
            run_dialog_recorder_loop(queue, provider, db_client, limiter, scheduler)
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()

    @patch(
        "backend.memory.runtime.scheduler.process_record",
        new_callable=AsyncMock,
    )
    async def test_requeues_active_item_when_primary_restarts(self, mock_proc):
        """Verify an in-flight record is cancelled and requeued."""
        started_event = asyncio.Event()
        released_event = asyncio.Event()

        async def slow_observation(**_: object) -> None:
            started_event.set()
            await released_event.wait()

        mock_proc.side_effect = slow_observation

        scheduler = DialogRecorderScheduler(SchedulingMode.IDLE)
        scheduler.signal_primary_done()
        queue = DialogRecorderQueue(max_depth=5)
        queued_item = DialogRecorderQueueItem(
            messages=["requeue me"],
            conversation_id="conv-1",
            session_id="sess-1",
        )
        queue.put(queued_item)
        provider = MagicMock()
        db_client = MagicMock()
        limiter = MagicMock()

        task = asyncio.create_task(
            run_dialog_recorder_loop(queue, provider, db_client, limiter, scheduler)
        )
        await asyncio.wait_for(started_event.wait(), timeout=0.1)

        scheduler.signal_primary_start()
        await asyncio.sleep(0.05)

        assert queue.size == 1
        requeued_item = await asyncio.wait_for(queue.get(), timeout=0.1)
        assert requeued_item == queued_item

        released_event.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestDistillerCoverage:
    """Tests for early distiller triggering from message coverage."""

    def test_counts_source_message_coverage_from_db_rows(self):
        """Verify source-message coverage is counted from DB row payloads."""
        rows = [
            ("o1", "summary", "[]", "[]", '["m1", "m2"]'),
            ("o2", "summary", "[]", "[]", '["m2", "m3", "m4"]'),
        ]
        assert _count_source_message_coverage(rows) == 4

    def test_counts_source_message_coverage_from_ipc_rows(self):
        """Verify source-message coverage is counted from IPC dict payloads."""
        rows = [
            {"id": "o1", "source_message_ids": ["m1", "m2"]},
            {"id": "o2", "source_message_ids": '["m2", "m3"]'},
        ]
        assert _count_source_message_coverage(rows) == 3

    def test_idle_trigger_runs_immediately_when_message_coverage_is_high(self):
        """Wide message coverage should trigger summary without another poll."""
        should_run, next_stable_poll_count = _should_run_extraction(
            unreflected_count=2,
            threshold=20,
            previous_unreflected_count=None,
            stable_poll_count=0,
            source_message_coverage=12,
        )
        assert should_run is True
        assert next_stable_poll_count == 0


class TestRunDistillerLoop:
    """Tests for run_graph_extractor_loop background task."""

    @patch(
        "backend.memory.graph_extractor.process_graph_extraction",
        new_callable=AsyncMock,
        return_value="ref-1",
    )
    @patch(
        "backend.memory.runtime.scheduler.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_runs_when_threshold_exceeded(
        self,
        mock_sleep,
        mock_proc,
    ):
        """Verify distiller runs when obs count exceeds threshold."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        provider = MagicMock()
        db_client = AsyncMock()
        db_client.fetch_unextracted_records.return_value = [{"id": "obs"}] * 25

        call_count = 0

        async def sleep_then_cancel(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        mock_sleep.side_effect = sleep_then_cancel

        with contextlib.suppress(asyncio.CancelledError):
            await run_graph_extractor_loop(provider, db_client, scheduler, threshold=20)

        mock_proc.assert_awaited_once()

    @patch(
        "backend.memory.graph_extractor.process_graph_extraction",
        new_callable=AsyncMock,
    )
    @patch(
        "backend.memory.runtime.scheduler.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_skips_when_below_threshold(
        self,
        mock_sleep,
        mock_proc,
    ):
        """Verify distiller skips when obs count below threshold."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        provider = MagicMock()
        db_client = AsyncMock()
        db_client.fetch_unextracted_records.return_value = [{"id": "obs"}] * 5

        call_count = 0

        async def sleep_then_cancel(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        mock_sleep.side_effect = sleep_then_cancel

        with contextlib.suppress(asyncio.CancelledError):
            await run_graph_extractor_loop(provider, db_client, scheduler, threshold=20)

        mock_proc.assert_not_awaited()

    def test_idle_trigger_runs_after_stable_polls(self):
        """Stable unreflected records should trigger a smaller summary."""
        should_run, stable_poll_count = _should_run_extraction(
            unreflected_count=3,
            threshold=20,
            previous_unreflected_count=None,
            stable_poll_count=0,
        )
        assert not should_run
        assert stable_poll_count == 1

        should_run, stable_poll_count = _should_run_extraction(
            unreflected_count=3,
            threshold=20,
            previous_unreflected_count=3,
            stable_poll_count=stable_poll_count,
        )
        assert should_run
        assert stable_poll_count == 0

    async def test_handles_cancelled_error(self):
        """Verify loop exits gracefully on cancellation."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        provider = MagicMock()
        db_client = AsyncMock()

        task = asyncio.create_task(run_graph_extractor_loop(provider, db_client, scheduler))
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()


class TestRunDistillerLoopRepeatedPolls:
    """Tests for repeated distiller polling on the canonical runtime path."""

    @patch(
        "backend.memory.graph_extractor.process_graph_extraction",
        new_callable=AsyncMock,
        return_value="ref-3",
    )
    @patch(
        "backend.memory.runtime.scheduler.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_requests_only_new_unreflected_observations(
        self,
        mock_sleep,
        mock_process_extraction,
    ):
        """Verify the loop triggers from a fixed batch of new records."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        provider = MagicMock()
        db_client = AsyncMock()
        db_client.fetch_unextracted_records.return_value = [{"id": "obs"}] * 20

        call_count = 0

        async def sleep_then_cancel(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        mock_sleep.side_effect = sleep_then_cancel

        with contextlib.suppress(asyncio.CancelledError):
            await run_graph_extractor_loop(
                provider=provider,
                db_client=db_client,
                scheduler=scheduler,
                threshold=20,
            )

        db_client.fetch_unextracted_records.assert_awaited_once_with(limit=20)
        mock_process_extraction.assert_awaited_once()

    @patch(
        "backend.memory.graph_extractor.process_graph_extraction",
        new_callable=AsyncMock,
        return_value="ref-4",
    )
    @patch(
        "backend.memory.runtime.scheduler.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_runs_after_stable_idle_batch_below_threshold(
        self,
        mock_sleep,
        mock_process_extraction,
    ):
        """The loop should synthesize after a stable idle batch below threshold."""
        scheduler = DialogRecorderScheduler(SchedulingMode.PARALLEL)
        provider = MagicMock()
        db_client = AsyncMock()
        db_client.fetch_unextracted_records.side_effect = [
            [{"id": "obs"}] * 6,
            [{"id": "obs"}] * 6,
        ]

        call_count = 0

        async def sleep_then_cancel(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError

        mock_sleep.side_effect = sleep_then_cancel

        with contextlib.suppress(asyncio.CancelledError):
            await run_graph_extractor_loop(
                provider=provider,
                db_client=db_client,
                scheduler=scheduler,
                threshold=20,
            )

        assert db_client.fetch_unextracted_records.await_count == 2
        mock_process_extraction.assert_awaited_once()
