"""Memory maintenance policy: hourly pruning of records and the graph.

Runs through the DB-proxy path only. Record retention: old records that
no assertion cites as evidence are deleted. Graph retention lives in the
prune_graph operation (long-invalidated assertions, orphan entities).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


logger = logging.getLogger(__name__)

RECORD_PRUNE_AGE_DAYS = 60


async def prune_unlinked_records(db_client: DBProxyClient) -> int:
    """Delete old records that no assertion cites as evidence."""
    result = await db_client.prune_unlinked_records(
        age_days=RECORD_PRUNE_AGE_DAYS,
    )
    return int(result.get("deleted", 0))


async def run_pruning_cycle(
    db_client: DBProxyClient,
) -> tuple[int, int, int]:
    """Run one full memory-maintenance cycle.

    Returns:
        Tuple of (assertions_deleted, records_deleted, entities_deleted).
    """
    records_deleted = await prune_unlinked_records(db_client)
    graph_result = await db_client.prune_graph()
    logger.debug("Graph prune result: %s", graph_result)
    return (
        int(graph_result.get("assertions_deleted", 0)),
        records_deleted,
        int(graph_result.get("entities_deleted", 0)),
    )
