"""Scheduled database backups inside the DB-proxy process.

The DB proxy is the only process holding the open databases, so the
schedule lives here and reuses the same ``VACUUM INTO`` path as the
manual admin backup. Each successful pass rotates old copies and stamps
the ``backup`` loop heartbeat so the health view can show liveness.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .handlers.admin import (
    LOOP_HEARTBEAT_KEY_PREFIX,
    perform_backup,
    prune_old_backups,
)


if TYPE_CHECKING:
    from backend.ipc.db_proxy import OperationRegistry
    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

BACKUP_INTERVAL_SECONDS = 24 * 3600.0
# First backup shortly after start, so a fresh deployment gets a copy
# without waiting a day.
BACKUP_INITIAL_DELAY_SECONDS = 600.0
BACKUP_KEEP_COPIES = 7


async def run_backup_loop(
    databases: dict[str, DatabaseManager],
    database_path: str,
    core_db: DatabaseManager,
    registry: OperationRegistry,
    interval_seconds: float = BACKUP_INTERVAL_SECONDS,
    initial_delay_seconds: float = BACKUP_INITIAL_DELAY_SECONDS,
    keep_copies: int = BACKUP_KEEP_COPIES,
) -> None:
    """Run daily consistent backups with rotation until cancelled.

    Args:
        databases: Open databases keyed by short name.
        database_path: Path of the core database file (locates backups/).
        core_db: Core database, used to stamp the loop heartbeat.
        registry: Registry that owns serialization for the live connections.
        interval_seconds: Delay between backup passes.
        initial_delay_seconds: Delay before the first pass.
        keep_copies: Newest copies retained per database name.
    """
    logger.info("Scheduled backup loop started")
    delay = initial_delay_seconds
    try:
        while True:
            await asyncio.sleep(delay)
            delay = interval_seconds
            try:

                def backup_pass() -> tuple[dict[str, str], int]:
                    paths = perform_backup(databases, database_path)
                    deleted = prune_old_backups(
                        databases,
                        database_path,
                        keep_copies,
                    )
                    core_db.execute(
                        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                        (
                            LOOP_HEARTBEAT_KEY_PREFIX + "backup",
                            datetime.now(tz=UTC).isoformat().encode(),
                        ),
                    )
                    core_db.commit()
                    return paths, deleted

                paths, deleted = await registry.run_serialized(backup_pass)
                logger.info(
                    "Scheduled backup written (%d databases, %d old "
                    "copies rotated out)",
                    len(paths),
                    deleted,
                )
            except Exception:
                logger.warning("Scheduled backup failed", exc_info=True)
    except asyncio.CancelledError:
        logger.info("Scheduled backup loop cancelled")
        raise
