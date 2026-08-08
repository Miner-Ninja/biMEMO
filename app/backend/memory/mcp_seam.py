"""Phase-two seam for relevant-memory resolution.

Provides internal functions callable by the MCP tools and a future
injection path. The external agent's identity is threaded through via
the agent_identity parameter.

This module contains NO search, ranking, or storage logic —
it delegates to existing DB proxy operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.fastapi_app.admin_db_client import AdminDBClient


MCP_CHANNEL_NAME = "agent"
DEFAULT_SEARCH_LIMIT = 20
MEMORY_SEARCH_TABLES = ("messages", "records")

# ── Hard limits (R5, R12) ──
MAX_READ_LIMIT = 50
MAX_RECENT_WINDOW = 50
MAX_OFFSET = 200


def _clamp_limit(limit: int) -> tuple[int, str | None]:
    """Clamp a read limit to MAX_READ_LIMIT; return (clamped, note|None)."""
    if limit > MAX_READ_LIMIT:
        return MAX_READ_LIMIT, f"limit clamped from {limit} to {MAX_READ_LIMIT}"
    return limit, None


def _clamp_offset(offset: int) -> tuple[int, str | None]:
    """Clamp an offset to MAX_OFFSET; return (clamped, note|None)."""
    if offset > MAX_OFFSET:
        return MAX_OFFSET, f"offset clamped from {offset} to {MAX_OFFSET}"
    return offset, None


def _is_broad_query(query: str) -> bool:
    """Return True if query is empty or a bare wildcard."""
    stripped = query.strip()
    return stripped == "" or stripped == "*"


async def resolve_relevant_memory(
    *,
    admin_client: AdminDBClient,
    query: str,
    agent_identity: str = MCP_CHANNEL_NAME,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Resolve relevant memory entries for one query.

    v1 implementation uses fulltext_search across messages, records,
    and summaries. On empty/wildcard query it falls back to recent
    entries. On zero matches it returns empty + a guidance note.

    Args:
        admin_client: DB Proxy client for executing operations.
        query: Natural language search query.
        agent_identity: Channel identifier of the requesting agent.
        limit: Maximum results per table.
        offset: Pagination offset.

    Returns:
        Dict with "results" (list) and optional "note".
    """
    limit, limit_note = _clamp_limit(limit)
    offset, offset_note = _clamp_offset(offset)
    notes: list[str] = []
    if limit_note:
        notes.append(limit_note)
    if offset_note:
        notes.append(offset_note)

    # Empty/wildcard → recent fallback (R1.2)
    if _is_broad_query(query):
        recent = await resolve_recent_memory(
            admin_client=admin_client,
            limit=limit,
            offset=offset,
        )
        recent["note"] = (
            "No keywords given; showing recent entries. "
            "Use search_memory with keywords for targeted results, "
            "or recent_memory for a bounded window."
        )
        if notes:
            recent["note"] += " " + " ".join(notes)
        return recent

    results: list[dict[str, Any]] = []
    graph_hits = await admin_client.find_graph_entry_points(query, limit)
    for hit in graph_hits:
        hit["table"] = "graph"
        hit["agent_identity"] = agent_identity
    results.extend(graph_hits)
    for table in MEMORY_SEARCH_TABLES:
        table_results = await admin_client.fulltext_search(
            query=query,
            table=table,
            limit=limit,
            offset=offset,
        )
        for hit in table_results:
            hit["table"] = table
            hit["agent_identity"] = agent_identity
        results.extend(table_results)

    if not results:
        return {
            "results": [],
            "note": (
                "No matches found. Try broader search terms, "
                "or use recent_memory to see the latest entries."
            ),
        }

    resp: dict[str, Any] = {"results": results}
    if notes:
        resp["note"] = " ".join(notes)
    return resp


async def resolve_recent_memory(
    *,
    admin_client: AdminDBClient,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Return the most recently created entries, newest-first.

    Bounded by MAX_RECENT_WINDOW. Reads across messages/records/summaries
    via Socket B.

    Args:
        admin_client: DB Proxy client for executing operations.
        limit: Maximum entries to return.
        offset: Pagination offset.

    Returns:
        Dict with "results" and optional "note".
    """
    limit, limit_note = _clamp_limit(min(limit, MAX_RECENT_WINDOW))
    offset, offset_note = _clamp_offset(offset)
    notes = [n for n in (limit_note, offset_note) if n]

    # Dedicated cross-table recent read (mcp_memory_recent, Socket B).
    entries = await admin_client.mcp_memory_recent(limit=limit, offset=offset)

    resp: dict[str, Any] = {"results": entries}
    if notes:
        resp["note"] = " ".join(notes)
    return resp


async def resolve_memory_entry(
    *,
    admin_client: AdminDBClient,
    entry_id: str,
    table: str = "messages",
) -> dict[str, Any]:
    """Fetch a single memory entry by id.

    Uses the dedicated by-id read (mcp_memory_get, direct ``WHERE id = ?``),
    so an entry is found regardless of how old it is.

    Args:
        admin_client: DB Proxy client for executing operations.
        entry_id: The entry identifier.
        table: The table to search (messages, records, summaries).

    Returns:
        Dict with "entry" (or None) and optional "note".
    """
    entry = await admin_client.mcp_memory_get(entry_id, table)
    if entry is None:
        return {
            "entry": None,
            "note": f"No entry found with id '{entry_id}' in '{table}'",
        }
    entry["table"] = table
    return {"entry": entry}


async def resolve_memory_stats(
    *,
    admin_client: AdminDBClient,
) -> dict[str, Any]:
    """Return aggregate memory statistics without entry content.

    Uses the dedicated stats read (mcp_memory_stats): totals, per-type
    counts, and the created-at date range.

    Args:
        admin_client: DB Proxy client for executing operations.

    Returns:
        Dict with totals, per-type counts, and date range.
    """
    stats = await admin_client.mcp_memory_stats()
    stats["note"] = "Statistics only — no entry content included."
    return stats


async def resolve_conversation_memory(
    *,
    admin_client: AdminDBClient,
    conversation_id: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Return entries for one conversation, newest-first, bounded.

    Args:
        admin_client: DB Proxy client for executing operations.
        conversation_id: The conversation to fetch.
        limit: Maximum entries to return.
        offset: Pagination offset.

    Returns:
        Dict with "results" and optional "note".
    """
    limit, limit_note = _clamp_limit(limit)
    offset, offset_note = _clamp_offset(offset)
    notes: list[str] = []
    if limit_note:
        notes.append(limit_note)
    if offset_note:
        notes.append(offset_note)

    messages = await admin_client.list_messages(
        limit=limit,
        offset=offset,
        conversation_id=conversation_id,
    )
    for m in messages:
        m["table"] = "message"

    resp: dict[str, Any] = {"results": messages}
    if notes:
        resp["note"] = " ".join(notes)
    return resp
