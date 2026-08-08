"""Dialog Recorder/Summary Distiller scheduling based on hardware profile.

Provides idle mode (minimal hardware) where background memory work
waits for primary model completion. Background tasks share a single
execution slot so Dialog Recorder and Summary Distiller do not compete
with each other on the same inference backend.

Scheduler-backed memory work now runs through the DB proxy path only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from backend.memory.dialog_recorder import process_record
from backend.memory.summary_distiller import process_summary


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.llm.ollama.provider import OllamaProvider
    from backend.memory.runtime.rate_limiter import (
        DialogRecorderQueue,
        RecordRateLimiter,
    )
logger = logging.getLogger(__name__)

DISTILLER_POLL_INTERVAL_SECONDS = 3.0
DISTILLER_IDLE_TRIGGER_THRESHOLD = 3
DISTILLER_IDLE_SOURCE_MESSAGE_THRESHOLD = 12
DISTILLER_STABLE_POLLS_REQUIRED = 2


class SchedulingMode(StrEnum):
    """Dialog Recorder scheduling mode based on hardware.

    IDLE: Wait for primary model before processing.
    PARALLEL: Allow background work without waiting for primary.
    """

    IDLE = "idle"
    PARALLEL = "parallel"


class DialogRecorderScheduler:
    """Schedules Dialog Recorder processing based on hardware profile.

    In idle mode, waits for a completion signal before
    allowing Dialog Recorder processing. In parallel mode,
    background processing may start immediately.

    Attributes:
        mode: Current scheduling mode.
    """

    def __init__(
        self,
        mode: SchedulingMode,
        idle_grace_period_seconds: float = 0.0,
    ) -> None:
        """Initialize the scheduler.

        Args:
            mode: Scheduling mode for Dialog Recorder.
            idle_grace_period_seconds: Delay after primary completion before
                background work may start in idle mode.
        """
        self.mode = mode
        self.idle_grace_period_seconds = idle_grace_period_seconds
        self._primary_done = asyncio.Event()
        self._active_background_task: asyncio.Task | None = None
        self._background_slot = asyncio.Lock()
        if mode == SchedulingMode.PARALLEL:
            self._primary_done.set()

    async def wait_for_primary(self) -> None:
        """Wait until primary model processing completes.

        In parallel mode, returns immediately.
        In idle mode, blocks until signal_primary_done()
        is called.
        """
        while True:
            await self._primary_done.wait()
            if (
                self.mode == SchedulingMode.PARALLEL
                or self.idle_grace_period_seconds <= 0
            ):
                return
            await asyncio.sleep(self.idle_grace_period_seconds)
            if self._primary_done.is_set():
                return

    def signal_primary_done(self) -> None:
        """Signal that the primary model has finished.

        Unblocks any waiting Dialog Recorder tasks.
        """
        self._primary_done.set()

    def signal_primary_start(self) -> None:
        """Signal that the primary model has started.

        In idle mode, blocks Dialog Recorder until primary
        finishes. In parallel mode, this is a no-op.
        """
        if self.mode == SchedulingMode.IDLE:
            self._primary_done.clear()
            if self._has_active_background_task():
                self._active_background_task.cancel()

    def _has_active_background_task(self) -> bool:
        """Return whether a live background task is currently tracked."""
        return (
            self._active_background_task is not None
            and not self._active_background_task.done()
        )

    def register_active_background_task(self, task: asyncio.Task) -> None:
        """Track a cancellable background task for preemption."""
        self._active_background_task = task

    def clear_active_background_task(self, task: asyncio.Task) -> None:
        """Clear the tracked background task if it matches ``task``."""
        if self._active_background_task is task:
            self._active_background_task = None

    async def acquire_background_slot(self) -> None:
        """Acquire exclusive access for a background memory task."""
        if self.mode == SchedulingMode.PARALLEL:
            return
        await self._background_slot.acquire()

    def release_background_slot(self) -> None:
        """Release exclusive access for a background memory task."""
        if self.mode == SchedulingMode.PARALLEL:
            return
        if self._background_slot.locked():
            self._background_slot.release()


async def _run_scheduled_background_task(
    scheduler: DialogRecorderScheduler,
    task: asyncio.Task[None],
    *,
    failure_log_message: str,
) -> None:
    """Run one background task under the scheduler slot and cancellation rules."""
    await scheduler.acquire_background_slot()
    scheduler.register_active_background_task(task)
    try:
        await task
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            task.cancel()
        raise
    except Exception:
        logger.exception(failure_log_message)
    finally:
        scheduler.clear_active_background_task(task)
        scheduler.release_background_slot()


def _should_run_distiller(
    unreflected_count: int,
    threshold: int,
    previous_unreflected_count: int | None,
    stable_poll_count: int,
    source_message_coverage: int = 0,
    idle_threshold: int = DISTILLER_IDLE_TRIGGER_THRESHOLD,
    idle_source_message_threshold: int = DISTILLER_IDLE_SOURCE_MESSAGE_THRESHOLD,
    stable_polls_required: int = DISTILLER_STABLE_POLLS_REQUIRED,
) -> tuple[bool, int]:
    """Decide whether the distiller should run on this poll.

    The distiller should run immediately when the hard threshold is met.
    Below that threshold, it may also run after a stable idle window when a
    smaller but meaningful batch of records has stopped growing.

    Args:
        unreflected_count: Current number of unreflected records.
        threshold: Hard trigger threshold.
        previous_unreflected_count: Previous poll count, if any.
        stable_poll_count: Number of consecutive stable polls so far.
        source_message_coverage: Number of unique source messages covered by the
            pending records.
        idle_threshold: Minimum count for idle-triggered synthesis.
        idle_source_message_threshold: Minimum unique source messages needed for
            early distiller work when record batches are larger.
        stable_polls_required: Stable polls required before idle trigger.

    Returns:
        Tuple of (should_run, next_stable_poll_count).
    """
    if unreflected_count <= 0:
        return False, 0
    if unreflected_count >= threshold:
        return True, 0
    has_enough_idle_work = (
        unreflected_count >= idle_threshold
        or source_message_coverage >= idle_source_message_threshold
    )
    if not has_enough_idle_work:
        return False, 0

    # If the pending records already cover a wide slice of the
    # conversation, synthesize immediately instead of waiting for one more
    # identical poll.
    if source_message_coverage >= idle_source_message_threshold:
        return True, 0

    if previous_unreflected_count == unreflected_count:
        next_stable_poll_count = stable_poll_count + 1
    else:
        next_stable_poll_count = 1

    if next_stable_poll_count >= stable_polls_required:
        return True, 0
    return False, next_stable_poll_count


def _count_source_message_coverage(rows: list[tuple] | list[dict]) -> int:
    """Count unique source messages represented by pending records."""
    source_message_ids: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            source_ids_raw = row.get("source_message_ids", "[]")
        elif len(row) >= 5:
            source_ids_raw = row[4]
        else:
            source_ids_raw = "[]"

        if isinstance(source_ids_raw, str):
            try:
                source_ids = json.loads(source_ids_raw)
            except json.JSONDecodeError:
                source_ids = []
        else:
            source_ids = source_ids_raw

        if isinstance(source_ids, list):
            source_message_ids.update(
                source_id for source_id in source_ids if isinstance(source_id, str)
            )
    return len(source_message_ids)


MAX_QUEUED_RECORD_ATTEMPTS = 3


async def _resolve_processed_queue_item(
    queue: DialogRecorderQueue,
    db_client: object,
    item: object,
    record_task: asyncio.Task,
) -> None:
    """Acknowledge or retry one processed queue item.

    A transient LLM failure re-queues the item (bounded attempts); every
    other outcome — success or a permanent skip — deletes the persisted
    queue rows.
    """
    from dataclasses import replace

    from backend.memory.dialog_recorder.runtime import DialogRecorderTransientError

    transient = (
        record_task.done()
        and not record_task.cancelled()
        and isinstance(record_task.exception(), DialogRecorderTransientError)
    )
    if transient and item.attempts + 1 < MAX_QUEUED_RECORD_ATTEMPTS:
        retry_item = replace(item, attempts=item.attempts + 1)
        try:
            await db_client.bump_queued_record_attempts(list(item.queue_ids))
        except Exception:
            logger.warning(
                "Failed to bump queued record attempts",
                exc_info=True,
            )
        queue.put_front(retry_item)
        return
    if transient:
        logger.warning(
            "Dialog Recorder giving up on queue item after %d attempts",
            item.attempts + 1,
        )
    try:
        await db_client.delete_queued_records(list(item.queue_ids))
    except Exception:
        logger.warning(
            "Failed to delete processed queue rows",
            exc_info=True,
        )


async def run_dialog_recorder_loop(
    queue: DialogRecorderQueue,
    provider: OllamaProvider,
    db_client: object,
    rate_limiter: RecordRateLimiter,
    scheduler: DialogRecorderScheduler,
    embed_fn: Callable | None = None,
    event_bus: object | None = None,
) -> None:
    """Run the Dialog Recorder background task loop through DB proxy calls."""
    logger.info("Dialog Recorder background loop started")
    try:
        while True:
            await scheduler.wait_for_primary()
            item = await queue.get()
            record_task = asyncio.create_task(
                process_record(
                    provider=provider,
                    db_client=db_client,
                    messages=item.messages,
                    conversation_id=item.conversation_id,
                    session_id=item.session_id,
                    source_message_ids=item.source_message_ids,
                    rate_limiter=rate_limiter,
                    injection_flagged=item.injection_flagged,
                    embed_fn=embed_fn,
                    event_bus=event_bus,
                )
            )
            try:
                await _run_scheduled_background_task(
                    scheduler,
                    record_task,
                    failure_log_message="Dialog Recorder processing failed",
                )
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                queue.put_front(item)
                continue
            await _resolve_processed_queue_item(
                queue,
                db_client,
                item,
                record_task,
            )
    except asyncio.CancelledError:
        logger.info("Dialog Recorder background loop cancelled")
        raise


async def run_distiller_loop(
    provider: OllamaProvider,
    db_client: object,
    scheduler: DialogRecorderScheduler,
    threshold: int = 20,
    event_bus: object | None = None,
) -> None:
    """Run the Summary Distiller background task loop through DB proxy calls."""
    logger.info("Summary Distiller background loop started")
    previous_unreflected_count: int | None = None
    stable_poll_count = 0
    try:
        while True:
            await asyncio.sleep(DISTILLER_POLL_INTERVAL_SECONDS)
            await scheduler.wait_for_primary()
            untainted = await db_client.fetch_untainted_records(
                limit=threshold,
            )
            unreflected_count = len(untainted)
            source_message_coverage = _count_source_message_coverage(untainted)
            should_run, stable_poll_count = _should_run_distiller(
                unreflected_count=unreflected_count,
                threshold=threshold,
                previous_unreflected_count=previous_unreflected_count,
                stable_poll_count=stable_poll_count,
                source_message_coverage=source_message_coverage,
            )
            logger.debug(
                "Distiller poll: unreflected=%d, coverage=%d, "
                "should_run=%s, stable_polls=%d",
                unreflected_count,
                source_message_coverage,
                should_run,
                stable_poll_count,
            )
            previous_unreflected_count = unreflected_count
            if should_run:
                distiller_task = asyncio.create_task(
                    process_summary(
                        provider=provider,
                        db_client=db_client,
                        event_bus=event_bus,
                    )
                )
                try:
                    await _run_scheduled_background_task(
                        scheduler,
                        distiller_task,
                        failure_log_message="Summary Distiller processing failed",
                    )
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                finally:
                    previous_unreflected_count = None
                    stable_poll_count = 0
    except asyncio.CancelledError:
        logger.info("Summary Distiller background loop cancelled")
        raise


PRUNING_INTERVAL_SECONDS = 3600.0


async def run_pruning_loop(db_client: object) -> None:
    """Run periodic memory pruning in the background.

    Deletes stale summaries and unlinked records hourly.

    Args:
        db_client: DB proxy client for storage operations.
    """
    from backend.memory.storage.pruning import run_pruning_cycle

    logger.info("Memory pruning loop started")
    try:
        while True:
            await asyncio.sleep(PRUNING_INTERVAL_SECONDS)
            try:
                summaries, records, conflicts = await run_pruning_cycle(db_client)
                if summaries or records or conflicts:
                    logger.info(
                        "Maintenance cycle: %d summaries, %d records, "
                        "%d conflicts surfaced",
                        summaries,
                        records,
                        conflicts,
                    )
            except Exception:
                logger.warning("Pruning cycle failed", exc_info=True)
    except asyncio.CancelledError:
        logger.info("Memory pruning loop cancelled")
        raise


async def run_reembed_loop(
    db_client: object,
    embed_fn: Callable,
    batch_size: int = 20,
    interval: float = 30.0,
) -> None:
    """Background loop that embeds records missing embeddings."""
    logger.info("Re-embed background loop started")
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                records = await db_client.fetch_unembedded_records(
                    limit=batch_size,
                )
                if not records:
                    continue
                logger.info(
                    "Re-embed: processing %d records",
                    len(records),
                )
                for obs in records:
                    try:
                        embedding = await embed_fn(obs["summary"])
                        if embedding:
                            await db_client.store_embedding(
                                obs["id"],
                                embedding,
                            )
                    except Exception:
                        logger.warning(
                            "Re-embed failed for record %s",
                            obs["id"][:8],
                            exc_info=True,
                        )
                    await asyncio.sleep(0.5)
            except Exception:
                logger.warning("Re-embed batch failed", exc_info=True)
    except asyncio.CancelledError:
        logger.info("Re-embed background loop cancelled")
        raise
