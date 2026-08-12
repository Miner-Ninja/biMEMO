"""Admin turn-trace: full per-turn observability assembled from persisted data.

Stitches one agent run together from the tables that already record it
(`agent_runs`, `agent_run_events`, `pending_actions`) plus, once persisted, the
new signal events (forced tools, requirements, tool_plan, reask decisions).
Read-only; reuses the Socket B admin client. Gated behind admin auth.
"""

from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from ..deps import _get_admin_client, _require_auth


if TYPE_CHECKING:
    from backend.channels.jwt_auth import TokenClaims

    from ..clients import AdminDBClient


trace_router = APIRouter(prefix="/admin/trace")

# agent_run_event types that carry the new per-turn signals (see A: persistence).
_SIGNAL_EVENT_TYPES = frozenset(
    {"forced_tools", "requirements", "tool_plan", "reask_decision"}
)


async def build_turn_trace(
    *,
    admin_client: AdminDBClient,
    run_id: str,
) -> dict[str, Any] | None:
    """Stitch one turn's full trace from the persisted tables, or None if absent.

    Each event already carries its parsed ``payload`` (the db-proxy record
    converter decodes payload_json), so no re-parsing is done here.
    """
    run = await admin_client.get_agent_run(run_id)
    if run is None:
        return None
    conversation_id = str(run.get("conversation_id", ""))
    events = await admin_client.list_agent_run_events(run_id)
    confirmations = await admin_client.list_pending_actions(run_id=run_id)
    tool_calls = [e for e in events if e.get("event_type") == "tool_status"]
    signals = {
        event["event_type"]: event.get("payload")
        for event in events
        if event.get("event_type") in _SIGNAL_EVENT_TYPES
    }
    return {
        "run": run,
        "conversation_id": conversation_id,
        "signals": signals,
        "tool_calls": tool_calls,
        "confirmations": confirmations,
        "events": events,
    }


@trace_router.get("/{run_id}")
async def get_turn_trace(
    run_id: str,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict[str, Any]:
    """Return the full assembled trace for one agent run."""
    trace = await build_turn_trace(admin_client=admin, run_id=run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return trace
