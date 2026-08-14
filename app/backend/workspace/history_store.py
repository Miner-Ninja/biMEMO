"""Workspace history store — persists and retrieves history entries.

Uses the DB proxy client to store/retrieve entries as JSON in a
dedicated table. Falls back to in-memory storage if DB is unavailable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .history_tracker import WorkspaceHistoryEntry


if TYPE_CHECKING:

    from backend.processes.orchestrator.client import DBProxyClient


logger = logging.getLogger(__name__)

HISTORY_TABLE = "workspace_history"


@dataclass
class WorkspaceHistoryStore:
    """Persist and retrieve workspace history entries.

    Stores entries in the database as JSON blobs. Each entry is keyed
    by (conversation_id, turn). Falls back to in-memory if DB ops fail.
    """

    db_client: DBProxyClient | None = None
    _memory_store: dict[str, list[WorkspaceHistoryEntry]] = None

    def __post_init__(self) -> None:
        if self._memory_store is None:
            self._memory_store = {}

    async def _ensure_table(self) -> None:
        """Create the workspace_history table if it doesn't exist."""
        if self.db_client is None:
            return
        try:
            await self.db_client.execute_sql(
                f"""
                CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
                    conversation_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, turn)
                )
                """
            )
        except Exception:
            logger.debug("Failed to create workspace_history table", exc_info=True)

    async def save(
        self,
        conversation_id: str,
        entry: WorkspaceHistoryEntry,
    ) -> None:
        """Save one history entry."""
        if self.db_client is None:
            self._memory_store.setdefault(conversation_id, []).append(entry)
            return

        await self._ensure_table()
        data_json = json.dumps(entry.to_dict(), ensure_ascii=False)
        try:
            await self.db_client.execute_sql(
                f"""
                INSERT OR REPLACE INTO {HISTORY_TABLE}
                    (conversation_id, turn, data)
                VALUES (?, ?, ?)
                """,
                (conversation_id, entry.turn, data_json),
            )
        except Exception:
            logger.debug("DB save failed, falling back to memory", exc_info=True)
            self._memory_store.setdefault(conversation_id, []).append(entry)

    async def get_all(self, conversation_id: str) -> list[WorkspaceHistoryEntry]:
        """Retrieve all history entries for a conversation."""
        if self.db_client is not None:
            try:
                rows = await self.db_client.query_sql(
                    f"SELECT data FROM {HISTORY_TABLE} "
                    f"WHERE conversation_id = ? ORDER BY turn",
                    (conversation_id,),
                )
                if rows:
                    return [
                        WorkspaceHistoryEntry.from_dict(json.loads(row["data"]))
                        for row in rows
                    ]
            except Exception:
                logger.debug("DB read failed, falling back to memory", exc_info=True)

        # Fallback to memory
        return list(self._memory_store.get(conversation_id, []))

    async def get_latest(
        self,
        conversation_id: str,
        last_n: int = 5,
    ) -> list[WorkspaceHistoryEntry]:
        """Get the most recent N history entries."""
        all_entries = await self.get_all(conversation_id)
        return all_entries[-last_n:]

    async def clear(self, conversation_id: str) -> None:
        """Clear all history for a conversation."""
        if self.db_client is not None:
            try:
                await self.db_client.execute_sql(
                    f"DELETE FROM {HISTORY_TABLE} WHERE conversation_id = ?",
                    (conversation_id,),
                )
            except Exception:
                logger.debug("DB clear failed", exc_info=True)
        self._memory_store.pop(conversation_id, None)
