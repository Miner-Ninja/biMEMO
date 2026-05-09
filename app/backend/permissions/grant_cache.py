"""In-process cache for assistant action grants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CachedActionGrant:
    """One cached permission decision."""

    scope_type: str
    scope_value: str
    capability: str
    decision: str
    channel: str = ""


class ActionGrantCache:
    """Small read-optimized cache for persisted action grants."""

    def __init__(self, grants: list[dict[str, Any]] | None = None) -> None:
        """Initialize with optional serialized grants from DB proxy."""
        self._decisions: dict[tuple[str, str, str, str], str] = {}
        if grants:
            self.replace(grants)

    def replace(self, grants: list[dict[str, Any]]) -> None:
        """Replace the cache from DB proxy grant records."""
        decisions: dict[tuple[str, str, str, str], str] = {}
        for grant in grants:
            cached = self._from_record(grant)
            decisions[
                (
                    cached.scope_type,
                    cached.scope_value,
                    cached.capability,
                    cached.channel,
                )
            ] = cached.decision
        self._decisions = decisions

    def remember(
        self,
        *,
        scope_type: str,
        scope_value: str,
        capability: str,
        decision: str,
        channel: str = "",
    ) -> None:
        """Add or update one cached grant."""
        self._decisions[(scope_type, scope_value, capability, channel)] = decision

    def find_decision(
        self,
        *,
        conversation_id: str,
        channel: str,
        tool_name: str,
    ) -> str | None:
        """Return the best matching decision for a tool call."""
        keys = (
            ("conversation", conversation_id, tool_name, channel),
            ("conversation", conversation_id, tool_name, ""),
            ("global", "*", tool_name, channel),
            ("global", "*", tool_name, ""),
        )
        for key in keys:
            decision = self._decisions.get(key)
            if decision is not None:
                return decision
        return None

    @staticmethod
    def _from_record(record: dict[str, Any]) -> CachedActionGrant:
        return CachedActionGrant(
            scope_type=str(record.get("scope_type", "")),
            scope_value=str(record.get("scope_value", "")),
            capability=str(record.get("capability", "")),
            decision=str(record.get("decision", "")),
            channel=str(record.get("channel", "")),
        )
