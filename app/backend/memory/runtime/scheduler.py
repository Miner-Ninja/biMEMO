"""Dialog Recorder/Summary Distiller scheduling based on hardware profile.

Provides idle mode (minimal hardware) where background memory work
waits for primary model completion. Background tasks share a single
execution slot so Dialog Recorder and Summary Distiller do not compete
with each other on the same inference backend.

Scheduler-backed memory work now runs through the DB proxy path only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from backend.memory.dialog_recorder import process_record


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

# A record whose extraction batches never produce a citing assertion
# would otherwise be re-fetched (oldest-first) and re-sent to the LLM on
# every trigger, forever. Mirror the recorder queue's retry bound. The
# counter is in-process, so a stuck record costs at most this many LLM
# batches per process lifetime.
MAX_EXTRACTION_ATTEMPTS_PER_RECORD = 3
_ATTEMPTS_MAP_PRUNE_SIZE = 1000


class ExtractionAttemptTracker:
    """Bound repeated graph-extraction attempts per record.

    Attributes:
        max_attempts: Attempts allowed before a record is set aside.
    """

    def __init__(
        self,
        max_attempts: int = MAX_EXTRACTION_ATTEMPTS_PER_RECORD,
    ) -> None:
        """Initialize an empty tracker.

        Args:
            max_attempts: Attempts allowed before a record is set aside.
        """
        self.max_attempts = max_attempts
        self._attempts: dict[str, int] = {}

    def _record_id(self, record: dict) -> str:
        """Return the id of one fetched record row."""
        return str(record.get("id", ""))

    def eligible(self, records: list[dict]) -> list[dict]:
        """Return the records still under the attempt bound."""
        return [
            record
            for record in records
            if self._attempts.get(self._record_id(record), 0) < self.max_attempts
        ]

    def register_batch(self, records: list[dict]) -> None:
        """Count one extraction attempt for every record in the batch.

        Records that extract successfully become cited and are never
        fetched again, so their counters simply go inert. The map is
        trimmed to the currently fetched ids once it grows past
        ``_ATTEMPTS_MAP_PRUNE_SIZE`` — still-uncited records are always
        part of the current fetch, so live counters survive the trim.
        """
        for record in records:
            record_id = self._record_id(record)
            self._attempts[record_id] = self._attempts.get(record_id, 0) + 1
        if len(self._attempts) > _ATTEMPTS_MAP_PRUNE_SIZE:
            current_ids = {self._record_id(record) for record in records}
            self._attempts = {
                record_id: count
                for record_id, count in self._attempts.items()
                if record_id in current_ids
            }


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


def _should_run_extraction(
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


async def run_graph_extractor_loop(
    provider: OllamaProvider,
    db_client: object,
    scheduler: DialogRecorderScheduler,
    threshold: int = 20,
    event_bus: object | None = None,
) -> None:
    """Run the graph extractor loop (stage-2 replacement for the distiller).

    Same poll cadence, trigger thresholds, and background-slot rules as
    the summary distiller; only the eligibility source and the processing
    function differ.
    """
    from backend.memory.graph_extractor import process_graph_extraction

    logger.info("Graph extractor background loop started")
    previous_count: int | None = None
    stable_poll_count = 0
    attempt_tracker = ExtractionAttemptTracker()
    exhausted_logged_count = 0
    try:
        while True:
            await asyncio.sleep(DISTILLER_POLL_INTERVAL_SECONDS)
            await scheduler.wait_for_primary()
            fetched = await db_client.fetch_unextracted_records(
                limit=threshold,
            )
            unextracted = attempt_tracker.eligible(fetched)
            exhausted_count = len(fetched) - len(unextracted)
            if exhausted_count > exhausted_logged_count:
                logger.warning(
                    "%d record(s) exceeded %d extraction attempts and are "
                    "set aside until restart",
                    exhausted_count,
                    attempt_tracker.max_attempts,
                )
            exhausted_logged_count = exhausted_count
            unextracted_count = len(unextracted)
            source_message_coverage = _count_source_message_coverage(
                unextracted,
            )
            should_run, stable_poll_count = _should_run_extraction(
                unreflected_count=unextracted_count,
                threshold=threshold,
                previous_unreflected_count=previous_count,
                stable_poll_count=stable_poll_count,
                source_message_coverage=source_message_coverage,
            )
            previous_count = unextracted_count
            if not should_run:
                continue
            attempt_tracker.register_batch(unextracted)
            extraction_task = asyncio.create_task(
                process_graph_extraction(
                    provider,
                    db_client,
                    unextracted,
                    event_bus=event_bus,
                )
            )
            try:
                await _run_scheduled_background_task(
                    scheduler,
                    extraction_task,
                    failure_log_message="Graph extraction failed",
                )
                if (
                    extraction_task.done()
                    and not extraction_task.cancelled()
                    and extraction_task.exception() is None
                ):
                    with contextlib.suppress(Exception):
                        await db_client.record_loop_heartbeat(
                            "graph_extractor",
                        )
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
            finally:
                previous_count = None
                stable_poll_count = 0
    except asyncio.CancelledError:
        logger.info("Graph extractor background loop cancelled")
        raise


PRUNING_INTERVAL_SECONDS = 3600.0


async def run_pruning_loop(db_client: object) -> None:
    """Run periodic memory pruning in the background.

    Deletes uncited old records, long-invalidated assertions, and
    orphaned entities hourly.

    Args:
        db_client: DB proxy client for storage operations.
    """
    from backend.memory.storage.pruning import run_pruning_cycle

    logger.info("Memory pruning loop started")
    try:
        while True:
            await asyncio.sleep(PRUNING_INTERVAL_SECONDS)
            try:
                (
                    assertions_deleted,
                    records_deleted,
                    entities_deleted,
                ) = await run_pruning_cycle(db_client)
                with contextlib.suppress(Exception):
                    await db_client.record_loop_heartbeat("pruning")
                if assertions_deleted or records_deleted or entities_deleted:
                    logger.info(
                        "Maintenance cycle: %d assertions, %d records, "
                        "%d entities removed",
                        assertions_deleted,
                        records_deleted,
                        entities_deleted,
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
