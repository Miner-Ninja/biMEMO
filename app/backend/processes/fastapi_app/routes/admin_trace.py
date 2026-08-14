"""Admin turn-trace: full per-turn observability assembled from persisted data.

Stitches one agent run together from the tables that already record it
(`agent_runs`, `agent_run_events`, `agent_confirmations`, `messages`) plus the new
signal events (forced tools, requirements, tool_plan, reask decisions). The list
endpoint shows recent runs with their request text so nothing has to be typed.
Read-only; reuses the Socket B admin client. Gated behind admin auth.
"""

from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import _get_admin_client, _require_auth


if TYPE_CHECKING:
    from backend.channels.jwt_auth import TokenClaims

    from ..clients import AdminDBClient


trace_router = APIRouter(prefix="/admin/trace")

# agent_run_event types that carry the new per-turn signals.
_SIGNAL_EVENT_TYPES = frozenset(
    {"forced_tools", "requirements", "tool_plan", "reask_decision"}
)


def _message_text(content: object) -> str:
    """Flatten one stored message's content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return " ".join(text for text in parts if text)
    return ""


def _latest_user_before(
    messages: list[dict[str, Any]],
    started_at: str,
) -> dict[str, Any] | None:
    """Return the newest user message at or before a run's start."""
    candidates = [
        message
        for message in messages
        if message.get("role") == "user"
        and str(message.get("created_at", "")) <= started_at
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda message: str(message.get("created_at", "")))


async def build_run_list(
    *,
    admin_client: AdminDBClient,
    limit: int,
) -> list[dict[str, Any]]:
    """Return recent runs (newest first) with their request text for the picker."""
    runs = await admin_client.list_agent_runs(limit=limit)
    conversations = await admin_client.list_conversations(limit=200)
    title_by_conversation = {
        str(conversation["id"]): str(conversation.get("title", ""))
        for conversation in conversations
    }
    messages_cache: dict[str, list[dict[str, Any]]] = {}
    items: list[dict[str, Any]] = []
    for run in runs:
        conversation_id = str(run.get("conversation_id", ""))
        if conversation_id and conversation_id not in messages_cache:
            messages_cache[conversation_id] = await admin_client.list_messages(
                conversation_id=conversation_id,
                limit=200,
            )
        user = _latest_user_before(
            messages_cache.get(conversation_id, []),
            str(run.get("created_at", "")),
        )
        items.append(
            {
                "id": run.get("id"),
                "conversation_id": conversation_id,
                "conversation_title": title_by_conversation.get(conversation_id, ""),
                "channel": run.get("channel"),
                "status": run.get("status"),
                "created_at": run.get("created_at"),
                "user_message": _message_text(user["content"])[:200] if user else "",
            }
        )
    return items


async def _turn_messages(
    *,
    admin_client: AdminDBClient,
    conversation_id: str,
    run: dict[str, Any],
) -> dict[str, Any]:
    """Return the user request and assistant answer for one run's turn."""
    if not conversation_id:
        return {"user": None, "assistant": None}
    messages = await admin_client.list_messages(
        conversation_id=conversation_id,
        limit=200,
    )
    started_at = str(run.get("created_at", ""))
    user = _latest_user_before(messages, started_at)
    assistants = [
        message
        for message in messages
        if message.get("role") == "assistant"
        and str(message.get("created_at", "")) >= started_at
    ]
    assistant = (
        min(assistants, key=lambda message: str(message.get("created_at", "")))
        if assistants
        else None
    )

    def _shape(message: dict[str, Any] | None) -> dict[str, Any] | None:
        if message is None:
            return None
        return {
            "content": _message_text(message.get("content")),
            "created_at": message.get("created_at"),
        }

    return {"user": _shape(user), "assistant": _shape(assistant)}


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
    confirmations = await admin_client.list_agent_confirmations(run_id)
    messages = await _turn_messages(
        admin_client=admin_client,
        conversation_id=conversation_id,
        run=run,
    )
    tool_calls = [e for e in events if e.get("event_type") == "tool_status"]
    signals = {
        event["event_type"]: event.get("payload")
        for event in events
        if event.get("event_type") in _SIGNAL_EVENT_TYPES
    }
    return {
        "run": run,
        "conversation_id": conversation_id,
        "messages": messages,
        "signals": signals,
        "tool_calls": tool_calls,
        "confirmations": confirmations,
        "events": events,
    }


@trace_router.get("")
async def list_recent_traces(
    limit: int = Query(default=50, ge=1, le=200),
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict[str, Any]:
    """Return recent runs with request text for the trace picker."""
    return {"runs": await build_run_list(admin_client=admin, limit=limit)}


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
