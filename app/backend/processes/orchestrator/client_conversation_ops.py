"""Conversation and message DB Proxy operations.

Provides module-level async functions that wrap DBProxyClient.send_operation
for message persistence, conversation management, context loading, and
full-text search. Each function receives a DBProxyClient instance as its
first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


# --- Message operations ---


async def save_message(
    client: DBProxyClient,
    conversation_id: str,
    role: str,
    content: str,
    channel: str | None = None,
    meta: str | None = None,
) -> str:
    """Save a message to the database.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation ID.
        role: Message role.
        content: Message text.
        channel: Optional channel identifier.
        meta: Optional JSON-encoded metadata.

    Returns:
        Generated message ID.
    """
    return await client._send_identifier_operation(
        "save_message",
        result_key="message_id",
        parameters={
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "channel": channel,
            "meta": meta,
        },
    )


async def load_recent_messages(
    client: DBProxyClient,
    conversation_id: str,
    limit: int = 50,
) -> list[dict[str, str]]:
    """Load recent messages for a conversation.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation ID.
        limit: Maximum number of messages.

    Returns:
        List of {"role": ..., "content": ...} dicts.
    """
    result = await client.send_operation(
        "load_recent_messages",
        {
            "conversation_id": conversation_id,
            "limit": limit,
        },
    )
    return result["messages"]


async def fulltext_search(
    client: DBProxyClient,
    query: str,
    table: str = "messages",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search messages or records via FTS5.

    Args:
        client: DB Proxy client instance.
        query: Search query string.
        table: Table to search ("messages" or "records").
        limit: Maximum results.

    Returns:
        List of search result dicts.
    """
    result = await client.send_operation(
        "fulltext_search",
        {
            "query": query,
            "table": table,
            "limit": limit,
        },
    )
    return result["results"]


async def list_messages(
    client: DBProxyClient,
    limit: int = 50,
    offset: int = 0,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """List stored messages with optional conversation filtering.

    Args:
        client: DB Proxy client instance.
        limit: Maximum number of messages.
        offset: Skip count.
        conversation_id: Optional conversation filter.

    Returns:
        List of message dicts.
    """
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if conversation_id:
        params["conversation_id"] = conversation_id
    return await client._send_list_operation(
        "list_messages",
        result_key="messages",
        parameters=params,
    )


# --- Context operations ---


async def load_context(
    client: DBProxyClient,
    session_id: str,
    conversation_id: str = "",
    summary_limit: int = 10,
    record_limit: int = 20,
    message_limit: int = 50,
    query: str | None = None,
    query_embedding: list[float] | None = None,
) -> dict[str, Any]:
    """Load combined context: summaries, records, messages.

    Args:
        client: DB Proxy client instance.
        session_id: Current session identifier.
        conversation_id: Conversation identifier for raw message history.
        summary_limit: Max summaries.
        record_limit: Max records.
        message_limit: Max messages.
        query: Optional text query for hybrid search.
        query_embedding: Optional embedding for vector search.

    Returns:
        Dict with "summaries", "records", "messages" keys,
        plus "search_results" when query is provided.
    """
    params: dict[str, Any] = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "summary_limit": summary_limit,
        "record_limit": record_limit,
        "message_limit": message_limit,
    }
    if query is not None:
        params["query"] = query
    if query_embedding is not None:
        params["query_embedding"] = query_embedding
    return await client.send_operation("load_context", params)


# --- Conversation operations ---


async def create_conversation(
    client: DBProxyClient,
    title: str = "",
) -> str:
    """Create a new conversation.

    Args:
        client: DB Proxy client instance.
        title: Conversation title.

    Returns:
        Conversation ID.
    """
    return await client._send_identifier_operation(
        "create_conversation",
        result_key="conversation_id",
        parameters={
            "title": title,
        },
    )


async def list_conversations(
    client: DBProxyClient,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List conversations.

    Args:
        client: DB Proxy client instance.
        limit: Maximum number.
        offset: Skip count.

    Returns:
        List of conversation dicts.
    """
    return await client._send_list_operation(
        "list_conversations",
        result_key="conversations",
        parameters={
            "limit": limit,
            "offset": offset,
        },
    )


async def delete_conversation(
    client: DBProxyClient,
    conversation_id: str,
) -> None:
    """Delete a conversation and cascade related data.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation to delete.
    """
    await client.send_operation(
        "delete_conversation",
        {
            "conversation_id": conversation_id,
        },
    )


async def update_conversation_title(
    client: DBProxyClient,
    conversation_id: str,
    title: str,
) -> dict[str, str] | None:
    """Update one conversation title and return the stored row.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation to update.
        title: New conversation title.

    Returns:
        Updated conversation row or None.
    """
    return await client._send_optional_dict_operation(
        "update_conversation_title",
        result_key="conversation",
        parameters={
            "conversation_id": conversation_id,
            "title": title,
        },
    )


async def update_conversation_processing_state(
    client: DBProxyClient,
    conversation_id: str,
    processing_status: str,
    processing_stage: str = "",
    processing_started_at: str | None = None,
) -> dict[str, Any] | None:
    """Update one conversation processing state and return the stored row.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation to update.
        processing_status: New processing status.
        processing_stage: New processing stage.
        processing_started_at: Timestamp when processing started.

    Returns:
        Updated conversation row or None.
    """
    return await client._send_optional_dict_operation(
        "update_conversation_processing_state",
        result_key="conversation",
        parameters={
            "conversation_id": conversation_id,
            "processing_status": processing_status,
            "processing_stage": processing_stage,
            "processing_started_at": processing_started_at,
        },
    )


async def reset_stuck_processing_states(client: DBProxyClient) -> int:
    """Reset all active processing rows to idle at orchestrator startup.

    Args:
        client: DB Proxy client instance.

    Returns:
        Number of rows that were cleared from active to idle.
    """
    result = await client.send_operation("reset_stuck_processing_states")
    return int(result.get("reset_count", 0))
