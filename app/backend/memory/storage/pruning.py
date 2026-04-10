"""Active pruning of stale memory entries.

Deletes outdated, orphaned, and unlinked records and summaries
from storage based on revalidation status and age thresholds.
Runs periodically alongside DialogRecorder and SummaryDistiller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


logger = logging.getLogger(__name__)

# --- Aging thresholds ---
SUMMARY_PRUNE_AGE_DAYS = 30
RECORD_PRUNE_AGE_DAYS = 60
PRUNE_STATUSES = frozenset({"outdated", "orphaned"})


async def prune_stale_summaries(
    db_client: DBProxyClient,
) -> int:
    """Delete summaries with outdated/orphaned status older than threshold.

    Args:
        db_client: DB proxy client for storage operations.

    Returns:
        Number of summaries deleted.
    """
    result = await db_client.send_operation(
        "prune_stale_summaries",
        {
            "statuses": list(PRUNE_STATUSES),
            "age_days": SUMMARY_PRUNE_AGE_DAYS,
        },
    )
    count = int(result.get("deleted", 0))
    if count:
        logger.info("Pruned %d stale summaries", count)
    return count


async def prune_unlinked_records(
    db_client: DBProxyClient,
) -> int:
    """Delete records with no linked summaries older than threshold.

    Args:
        db_client: DB proxy client for storage operations.

    Returns:
        Number of records deleted.
    """
    result = await db_client.send_operation(
        "prune_unlinked_records",
        {"age_days": RECORD_PRUNE_AGE_DAYS},
    )
    count = int(result.get("deleted", 0))
    if count:
        logger.info("Pruned %d unlinked records", count)
    return count


async def run_pruning_cycle(
    db_client: DBProxyClient,
) -> tuple[int, int]:
    """Run one full pruning cycle.

    Args:
        db_client: DB proxy client for storage operations.

    Returns:
        Tuple of (summaries_deleted, records_deleted).
    """
    summaries_deleted = await prune_stale_summaries(db_client)
    records_deleted = await prune_unlinked_records(db_client)
    return summaries_deleted, records_deleted
