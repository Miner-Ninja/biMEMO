"""Memory-related DB Proxy operations for records, summaries, and embeddings.

Provides module-level async functions that wrap DBProxyClient.send_operation
for record CRUD, summary management, embedding storage, and vector table
initialization. Each function receives a DBProxyClient instance as its
first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def save_record(
    client: DBProxyClient,
    conversation_id: str,
    session_id: str,
    summary: str,
    key_facts: list[str] | None = None,
    action_items: list[str] | None = None,
    memory_kinds: list[str] | None = None,
    source_message_ids: list[str] | None = None,
    tainted: bool = False,
) -> str:
    """Save an record.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation ID.
        session_id: Session ID.
        summary: Record summary.
        key_facts: List of key facts.
        action_items: List of action items.
        memory_kinds: Dialog Recorder memory categories for this record.
        source_message_ids: Source message IDs used for compression.
        tainted: Whether the source input was injection-flagged. Tainted
            records are stored for session-scoped reads but excluded from
            distillation and search.

    Returns:
        Record ID.
    """
    return await client._send_identifier_operation(
        "save_record",
        result_key="record_id",
        parameters={
            "conversation_id": conversation_id,
            "session_id": session_id,
            "summary": summary,
            "key_facts": key_facts or [],
            "action_items": action_items or [],
            "memory_kinds": memory_kinds or [],
            "source_message_ids": source_message_ids or [],
            "tainted": tainted,
        },
    )


async def save_queued_record(
    client: DBProxyClient,
    conversation_id: str,
    session_id: str,
    messages: list[str],
    source_message_ids: list[str] | None = None,
    injection_flagged: bool = False,
) -> str:
    """Persist one pending dialog-recorder queue item.

    Returns:
        Queue row ID.
    """
    return await client._send_identifier_operation(
        "save_queued_record",
        result_key="queue_id",
        parameters={
            "conversation_id": conversation_id,
            "session_id": session_id,
            "messages": messages,
            "source_message_ids": source_message_ids or [],
            "injection_flagged": injection_flagged,
        },
    )


async def delete_queued_records(
    client: DBProxyClient,
    queue_ids: list[str],
) -> None:
    """Delete processed dialog-recorder queue rows."""
    if not queue_ids:
        return
    await client.send_operation(
        "delete_queued_records",
        {"queue_ids": queue_ids},
    )


async def list_queued_records(
    client: DBProxyClient,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List pending dialog-recorder queue rows, oldest first."""
    result = await client.send_operation(
        "list_queued_records",
        {"limit": limit},
    )
    return result["items"]


async def bump_queued_record_attempts(
    client: DBProxyClient,
    queue_ids: list[str],
) -> None:
    """Increment the attempt counter on pending queue rows."""
    if not queue_ids:
        return
    await client.send_operation(
        "bump_queued_record_attempts",
        {"queue_ids": queue_ids},
    )


async def store_embedding(
    client: DBProxyClient,
    record_id: str,
    embedding: list[float],
) -> bool:
    """Store an embedding vector for an record.

    Args:
        client: DB Proxy client instance.
        record_id: Record ID.
        embedding: Float vector to store.

    Returns:
        True if stored successfully.
    """
    return await client._send_bool_operation(
        "store_embedding",
        result_key="ok",
        parameters={
            "record_id": record_id,
            "embedding": embedding,
        },
    )


async def fetch_untainted_records(
    client: DBProxyClient,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch untainted records for distiller.

    Args:
        client: DB Proxy client instance.
        limit: Maximum number of records.

    Returns:
        List of record dicts with id, summary, key_facts.
    """
    return await client._send_list_operation(
        "fetch_untainted_records",
        result_key="records",
        parameters={
            "limit": limit,
        },
    )


async def count_distillations(
    client: DBProxyClient,
) -> int:
    """Count total summaries in the database.

    Args:
        client: DB Proxy client instance.

    Returns:
        Number of summaries.
    """
    return await client._send_int_operation(
        "count_distillations",
        result_key="count",
    )


async def save_summary(
    client: DBProxyClient,
    main_point: str,
    based_on: list[str] | None = None,
    trust_level: str = "",
    summary_scope: str = "long_term",
    source_record_ids: list[str] | None = None,
    record_relations: list[dict[str, str]] | None = None,
) -> str:
    """Save a summary.

    Args:
        client: DB Proxy client instance.
        main_point: Summary main point text.
        based_on: Supporting reasons list.
        trust_level: Trust level label.
        summary_scope: Time horizon for the summary.
        source_record_ids: Source record IDs.
        record_relations: Typed relations to source records.

    Returns:
        Summary ID.
    """
    return await client._send_identifier_operation(
        "save_summary",
        result_key="summary_id",
        parameters={
            "main_point": main_point,
            "based_on": based_on or [],
            "trust_level": trust_level,
            "summary_scope": summary_scope,
            "source_record_ids": source_record_ids or [],
            "record_relations": record_relations or [],
        },
    )


async def merge_summary_sources(
    client: DBProxyClient,
    summary_id: str,
    main_point: str = "",
    based_on: list[str] | None = None,
    trust_level: str = "",
    summary_scope: str = "long_term",
    trust_level_score: int | None = None,
    source_record_ids: list[str] | None = None,
    record_relations: list[dict[str, str]] | None = None,
) -> bool:
    """Attach new source records to an existing summary.

    Args:
        client: DB Proxy client instance.
        summary_id: Existing summary identifier.
        main_point: Replacement main point candidate.
        based_on: Replacement supporting reasons.
        trust_level: Replacement trust label.
        summary_scope: Replacement time horizon for the summary.
        trust_level_score: Replacement trust score.
        source_record_ids: New source record identifiers.
        record_relations: Typed relations to the new records.

    Returns:
        True when the summary was updated.
    """
    return await client._send_bool_operation(
        "merge_summary_sources",
        result_key="updated",
        parameters={
            "summary_id": summary_id,
            "main_point": main_point,
            "based_on": based_on or [],
            "trust_level": trust_level,
            "summary_scope": summary_scope,
            "trust_level_score": trust_level_score,
            "source_record_ids": source_record_ids or [],
            "record_relations": record_relations or [],
        },
    )


async def list_summaries(
    client: DBProxyClient,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List summaries.

    Args:
        client: DB Proxy client instance.
        limit: Maximum number.
        offset: Skip count.

    Returns:
        List of summary dicts.
    """
    return await client._send_list_operation(
        "list_summaries",
        result_key="summaries",
        parameters={
            "limit": limit,
            "offset": offset,
        },
    )


async def list_records(
    client: DBProxyClient,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List records with full metadata.

    Args:
        client: DB Proxy client instance.
        limit: Maximum number.
        offset: Skip count.

    Returns:
        List of record dicts.
    """
    return await client._send_list_operation(
        "list_records",
        result_key="records",
        parameters={
            "limit": limit,
            "offset": offset,
        },
    )


async def list_summaries_full(
    client: DBProxyClient,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List summaries with source record metadata.

    Args:
        client: DB Proxy client instance.
        limit: Maximum number.
        offset: Skip count.

    Returns:
        List of summary dicts with source record metadata.
    """
    return await client._send_list_operation(
        "list_summaries_full",
        result_key="summaries",
        parameters={
            "limit": limit,
            "offset": offset,
        },
    )


async def count_memory_entries(
    client: DBProxyClient,
) -> dict[str, int]:
    """Return counts for stored memory entries.

    Args:
        client: DB Proxy client instance.

    Returns:
        Dict with counts for messages, records, and summaries.
    """
    result = await client.send_operation("count_memory_entries")
    return {
        "messages": int(result.get("messages", 0)),
        "records": int(result.get("records", 0)),
        "summaries": int(result.get("summaries", 0)),
    }


async def init_vector_table(
    client: DBProxyClient,
    embedding_model: str,
    dimensions: int,
) -> dict[str, Any]:
    """Initialize or recreate the vector table for embeddings.

    Compares with stored metadata; recreates if model or
    dimensions changed.

    Args:
        client: DB Proxy client instance.
        embedding_model: Name of the embedding model.
        dimensions: Vector dimensionality from probe.

    Returns:
        Dict with 'ok' and 'recreated' keys.
    """
    return await client.send_operation(
        "init_vector_table",
        {
            "embedding_model": embedding_model,
            "dimensions": dimensions,
        },
    )


async def fetch_unembedded_records(
    client: DBProxyClient,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch records that have no embedding yet.

    Args:
        client: DB Proxy client instance.
        limit: Maximum number of records.

    Returns:
        List of dicts with 'id' and 'summary' keys.
    """
    return await client._send_list_operation(
        "fetch_unembedded_records",
        result_key="records",
        parameters={
            "limit": limit,
        },
    )


async def save_entity(
    client: DBProxyClient,
    entity_type: str,
    canonical_name: str,
    aliases: list[str] | None = None,
) -> str:
    """Get or create one graph entity; returns its id."""
    return await client._send_identifier_operation(
        "save_entity",
        result_key="entity_id",
        parameters={
            "entity_type": entity_type,
            "canonical_name": canonical_name,
            "aliases": aliases or [],
        },
    )


async def save_assertion(
    client: DBProxyClient,
    *,
    subject_entity_id: str,
    relation: str,
    assertion_text: str,
    object_entity_id: str | None = None,
    object_value: str | None = None,
    trust_level: str = "medium",
    assertion_scope: str = "long_term",
    valid_from: str | None = None,
    source_record_ids: list[str] | None = None,
) -> str:
    """Store one assertion edge; returns its id."""
    return await client._send_identifier_operation(
        "save_assertion",
        result_key="assertion_id",
        parameters={
            "subject_entity_id": subject_entity_id,
            "relation": relation,
            "assertion_text": assertion_text,
            "object_entity_id": object_entity_id,
            "object_value": object_value,
            "trust_level": trust_level,
            "assertion_scope": assertion_scope,
            "valid_from": valid_from,
            "source_record_ids": source_record_ids or [],
        },
    )


async def invalidate_assertion(
    client: DBProxyClient,
    assertion_id: str,
    invalidated_by_assertion_id: str | None = None,
) -> bool:
    """Mark one assertion as no longer valid."""
    result = await client.send_operation(
        "invalidate_assertion",
        {
            "assertion_id": assertion_id,
            "invalidated_by_assertion_id": invalidated_by_assertion_id,
        },
    )
    return bool(result.get("invalidated", False))


async def migrate_summaries_to_assertions(
    client: DBProxyClient,
) -> dict[str, Any]:
    """Run the idempotent legacy-summaries migration."""
    return await client.send_operation("migrate_summaries_to_assertions")


async def get_entity(
    client: DBProxyClient,
    entity_id: str,
) -> dict[str, Any] | None:
    """Load one entity by id."""
    result = await client.send_operation(
        "get_entity",
        {"entity_id": entity_id},
    )
    return result.get("entity")


async def find_entities(
    client: DBProxyClient,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Find entities by full-text query over names and aliases."""
    result = await client.send_operation(
        "find_entities",
        {"query": query, "limit": limit},
    )
    return result["entities"]


async def list_assertions_for_entity(
    client: DBProxyClient,
    entity_id: str,
    valid_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List assertions where the entity is subject or object."""
    result = await client.send_operation(
        "list_assertions_for_entity",
        {"entity_id": entity_id, "valid_only": valid_only, "limit": limit},
    )
    return result["assertions"]


async def find_graph_entry_points(
    client: DBProxyClient,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Find labeled entity/assertion entry points for a query."""
    result = await client.send_operation(
        "find_graph_entry_points",
        {"query": query, "limit": limit},
    )
    return result["entry_points"]
