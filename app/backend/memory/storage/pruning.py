"""Pruning of superseded memory entries.

Summaries are removed only when a newer summary has already outdated them,
after a grace period. Orphaned and conflicted summaries are retained: their
raw evidence stays reachable through the lower memory tiers (records and
source messages), and conflicts are handled by reconciliation. Unlinked,
aged records are pruned separately. Runs periodically alongside
DialogRecorder and SummaryDistiller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


logger = logging.getLogger(__name__)

# --- Retention thresholds ---
OUTDATED_GRACE_DAYS = 30
RECORD_PRUNE_AGE_DAYS = 60

# --- Revalidation statuses retention acts on ---
PRUNABLE_STATUSES = frozenset({"outdated", "orphaned"})


def should_prune_summary(
    *,
    status: str,
    age_days: int,
    grace_days: int,
) -> bool:
    """Decide whether one summary should be pruned.

    A summary is removed only when it is no longer trustworthy — superseded
    by newer evidence (``outdated``) or left without any active supporting
    record (``orphaned``) — and only after the grace period. Conflicted,
    partially supported, fresh, and merely aged (``stale``) summaries are
    retained; the ranker already demotes them.

    Args:
        status: The derived revalidation status.
        age_days: The summary's age in whole days.
        grace_days: Grace period before removing an untrustworthy summary.

    Returns:
        True when the summary should be deleted.
    """
    return status in PRUNABLE_STATUSES and age_days > grace_days


async def prune_stale_summaries(
    db_client: DBProxyClient,
) -> int:
    """Delete summaries selected by scope-aware retention.

    Args:
        db_client: DB proxy client for storage operations.

    Returns:
        Number of summaries deleted.
    """
    result = await db_client.send_operation(
        "prune_stale_summaries",
        {"grace_days": OUTDATED_GRACE_DAYS},
    )
    count = int(result.get("deleted", 0))
    if count:
        logger.info("Pruned %d summaries", count)
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


async def surface_conflicted_summaries(
    db_client: DBProxyClient,
) -> int:
    """Record a pending-conflict signal for each newly conflicted summary.

    Conflicted summaries are only surfaced for the user (or the distiller)
    to resolve, never deleted or reclassified here. Deduplication is done
    server-side by the idempotent ``conflicted_summary_flag`` operation, so
    a summary that already has an unresolved signal is not surfaced again.

    Args:
        db_client: DB proxy client for storage operations.

    Returns:
        Number of summaries newly surfaced.
    """
    listed = await db_client.send_operation("list_conflicted_summaries", {})
    conflicted = listed.get("summaries", [])

    surfaced = 0
    for item in conflicted:
        summary_id = str(item.get("summary_id", ""))
        if not summary_id:
            continue
        result = await db_client.send_operation(
            "conflicted_summary_flag",
            {
                "summary_id": summary_id,
                "main_point": str(item.get("main_point", "")),
            },
        )
        surfaced += int(result.get("surfaced", 0))

    if surfaced:
        logger.info("Surfaced %d conflicted summaries", surfaced)
    return surfaced


async def run_pruning_cycle(
    db_client: DBProxyClient,
) -> tuple[int, int, int]:
    """Run one full memory-maintenance cycle.

    Args:
        db_client: DB proxy client for storage operations.

    Returns:
        Tuple of (summaries_deleted, records_deleted, conflicts_surfaced).
    """
    summaries_deleted = await prune_stale_summaries(db_client)
    records_deleted = await prune_unlinked_records(db_client)
    conflicts_surfaced = await surface_conflicted_summaries(db_client)
    return summaries_deleted, records_deleted, conflicts_surfaced
