"""Phase-two seam for relevant-memory resolution.

Provides a single internal function callable by both the MCP
search_memory tool and a future injection path. The external
agent's identity is threaded through via the agent_identity parameter.

This module contains NO search, ranking, or storage logic —
it delegates to existing DB proxy operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.fastapi_app.admin_db_client import AdminDBClient


MCP_CHANNEL_NAME = "agent"
DEFAULT_SEARCH_LIMIT = 20
MEMORY_SEARCH_TABLES = ("messages", "records", "summaries")


async def resolve_relevant_memory(
    *,
    admin_client: AdminDBClient,
    query: str,
    agent_identity: str = MCP_CHANNEL_NAME,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    """Resolve relevant memory entries for one query.

    This is the phase-two seam: both the MCP search_memory tool and
    a future injection path call this function. The agent_identity
    parameter carries the external agent's channel identifier.

    v1 implementation uses fulltext_search across messages, records,
    and summaries. It can be upgraded to the full recall pipeline
    later without changing callers.

    Args:
        admin_client: DB Proxy client for executing operations.
        query: Natural language search query.
        agent_identity: Channel identifier of the requesting agent.
        limit: Maximum results per table.

    Returns:
        List of memory hit dicts, each with table type, rowid,
        agent_identity, and snippet from the search results.
    """
    results: list[dict[str, Any]] = []

    for table in MEMORY_SEARCH_TABLES:
        table_results = await admin_client.fulltext_search(
            query=query,
            table=table,
            limit=limit,
        )
        for hit in table_results:
            hit["table"] = table
            hit["agent_identity"] = agent_identity
        results.extend(table_results)

    return results
