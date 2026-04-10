"""Data models for evidence chain processing.

Defines the dataclass and protocol used by the evidence chain module
to represent collected summary support data and the minimal admin
client surface needed for evidence bundle loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class EvidenceBundle:
    """Collected summary support data for evidence-based recall.

    Attributes:
        summary: Summary record used as the top-level memory entry.
        records: Records directly linked to the summary.
        source_messages: Original messages linked to the records.
    """

    summary: dict[str, Any]
    records: list[dict[str, Any]]
    source_messages: list[dict[str, Any]]


class EvidenceAdminClient(Protocol):
    """Minimal admin client surface needed for evidence bundle loading."""

    async def count_memory_entries(self) -> dict[str, int]:
        """Return counts for messages, records, and summaries."""

    async def list_summaries_full(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return full summary records."""

    async def list_records(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return record records."""

    async def list_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return message records."""
