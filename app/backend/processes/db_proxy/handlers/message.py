"""Handler factories for message persistence, context loading, and fulltext search.

Provides make_save_message_handler, make_load_recent_messages_handler,
make_fulltext_search_handler, and make_load_context_handler. These handlers
manage the message lifecycle and the hybrid search-augmented context retrieval
pipeline used by the orchestrator.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from backend.ipc.models import extract_text_content
from backend.memory.classification.question_router import (
    route_memory_question,
)
from backend.memory.retrieval.fulltext import (
    index_message,
)

from ..common import (
    OperationHandler,
    build_in_clause_query,
    memory_weighted_search_limits,
    parse_content,
    table_exists,
    validate_params,
)
from ..params import (
    FulltextSearchParams,
    LoadContextParams,
    LoadRecentMessagesParams,
    SaveConversationSummaryParams,
    SaveMessageParams,
)


if TYPE_CHECKING:
    from pydantic import BaseModel

    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)


def _validate_params(
    params: dict[str, Any] | None,
    model_class: type[BaseModel],
) -> BaseModel:
    """Validate operation parameters against a Pydantic model."""
    return validate_params(params, model_class)


def _build_in_clause_query(base_sql: str, item_count: int) -> str:
    """Build a SQL query with a validated ``IN (?, ?, ...)`` clause."""
    return build_in_clause_query(base_sql, item_count)


def _table_exists(db: DatabaseManager, table_name: str) -> bool:
    """Return whether a table exists in the current SQLite schema."""
    return table_exists(db, table_name)


def _memory_weighted_search_limits(
    record_limit: int,
    summary_limit: int,
    message_search_limit: int,
    memory_query: bool,
    role_filter: str | None,
) -> tuple[int, int, int]:
    """Return query-aware search limits for context loading.

    Memory-seeking queries should favor semantic and long-term memory
    retrieval over raw message hit volume. Role-filtered message search
    still gets a small bump to offset post-filter losses.
    """
    return memory_weighted_search_limits(
        record_limit,
        summary_limit,
        message_search_limit,
        memory_query,
        role_filter,
    )


def _message_text_for_routing(raw_content: str) -> str:
    """Convert stored message content into plain text for route checks."""
    parsed_content = parse_content(raw_content)
    if isinstance(parsed_content, str):
        return parsed_content
    return extract_text_content(parsed_content)


def _should_skip_retrieved_message(
    *,
    raw_content: str,
    current_query_is_memory_query: bool,
) -> bool:
    """Suppress cross-session retrieved messages that are memory-meta prompts.

    When the current request already asks to recall memory, old recall/meta
    questions are usually noise. They tend to pull the model toward answering
    the memory mechanics instead of the remembered topic itself.
    """
    if not current_query_is_memory_query:
        return False
    message_text = _message_text_for_routing(raw_content)
    if not message_text.strip():
        return False
    return route_memory_question(message_text).memory_query


def _load_conversation_summary(
    db: DatabaseManager,
    conversation_id: str,
) -> str:
    """Load the persisted S-zone summary for one conversation."""
    row = db.execute(
        "SELECT conversation_summary FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "")


def _load_base_context_rows(
    db: DatabaseManager,
    p: LoadContextParams,
) -> tuple[list[dict[str, Any]], list[tuple], list[tuple]]:
    """Load the baseline summaries, records, and chronological messages."""
    return (
        _load_revalidated_summary_rows(
            db,
            limit=p.summary_limit,
        ),
        db.execute(
            "SELECT id, summary, key_facts, action_items, created_at FROM records "
            "WHERE tainted = 0 OR session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (p.session_id, p.record_limit),
        ).fetchall(),
        db.execute(
            "SELECT id, role, content, conversation_id, created_at FROM messages "
            "WHERE conversation_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (p.conversation_id, p.message_limit),
        ).fetchall(),
    )


def _load_revalidated_summary_rows(
    db: DatabaseManager,
    *,
    limit: int,
    summary_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Load revalidated summary rows with the shared store helper."""
    from backend.memory.summary_distiller.store import list_revalidated_distillations

    return list_revalidated_distillations(
        db,
        limit=limit,
        summary_ids=summary_ids,
    )


def _build_base_context_result(
    *,
    summary_rows: list[dict[str, Any]],
    records: list[tuple],
    messages: list[tuple],
) -> dict[str, Any]:
    """Build the baseline context payload before query-specific search augmentation."""
    return {
        "summaries": [
            {
                "id": row["id"],
                "main_point": row["main_point"],
                "scope": row["scope"],
                "source_record_ids": row["source_record_ids"],
                "record_relations": row["record_relations"],
                "created_at": row["created_at"],
                "revalidation_status": row["revalidation_status"],
                "effective_trust_level_score": row["effective_trust_level_score"],
            }
            for row in summary_rows
        ],
        "records": [
            {
                "id": record[0],
                "summary": record[1],
                "key_facts": record[2],
                "action_items": record[3],
                "created_at": record[4],
            }
            for record in records
        ],
        "messages": [
            {"role": message[1], "content": message[2]} for message in messages
        ],
    }


def _log_base_context_load(
    *,
    session_id: str,
    query: str | None,
    records: list[tuple],
    messages: list[tuple],
    summary_rows: list[dict[str, Any]],
) -> None:
    """Log the size of the baseline context load before search augmentation."""
    logger.debug(
        "load_context: session=%s, query=%r, obs=%d, msgs=%d, refs=%d",
        session_id[:8],
        query,
        len(records),
        len(messages),
        len(summary_rows),
    )


def _run_record_context_search(
    *,
    db: DatabaseManager,
    query: str,
    query_embedding: list[float] | None,
    limit: int,
) -> list[Any]:
    """Run the record-layer hybrid search used by load_context."""
    from backend.memory.retrieval import hybrid_search

    return hybrid_search(
        db,
        query,
        query_embedding,
        "records",
        limit=limit,
    )


def _serialize_search_results(
    search_results: list[Any],
) -> list[dict[str, Any]]:
    """Convert hybrid search results into the API response shape."""
    return [
        {
            "identifier": result.identifier,
            "source": result.source,
            "score": result.score,
        }
        for result in search_results
    ]


def _append_missing_record_search_hits(
    *,
    db: DatabaseManager,
    result: dict[str, Any],
    records: list[tuple],
    search_results: list[Any],
) -> None:
    """Append search-matched records that were not already in the baseline load."""
    loaded_ids = {record[0] for record in records}
    missing_ids = [
        search_result.identifier
        for search_result in search_results
        if search_result.identifier not in loaded_ids
    ]
    if not missing_ids:
        return

    extra_records = db.execute(
        _build_in_clause_query(
            "SELECT id, summary, key_facts, action_items, created_at "
            "FROM records WHERE id IN",
            len(missing_ids),
        ),
        tuple(missing_ids),
    ).fetchall()
    for record in extra_records:
        result["records"].append(
            {
                "id": record[0],
                "summary": record[1],
                "key_facts": record[2],
                "action_items": record[3],
                "created_at": record[4],
            }
        )


def _build_summary_row_map(
    summary_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build one lookup map for loaded summary rows keyed by summary id."""
    return {
        str(row["id"]): {
            "id": row["id"],
            "main_point": row["main_point"],
            "source_record_ids": row["source_record_ids"],
            "record_relations": row["record_relations"],
            "created_at": row["created_at"],
            "revalidation_status": row["revalidation_status"],
            "effective_trust_level_score": row["effective_trust_level_score"],
        }
        for row in summary_rows
    }


def _extend_summary_row_map_with_search_hits(
    *,
    db: DatabaseManager,
    summary_row_map: dict[str, dict[str, Any]],
    summary_search_results: list[Any],
) -> None:
    """Load and append summary rows that were found by search but not preloaded."""
    missing_summary_ids = [
        search_result.identifier
        for search_result in summary_search_results
        if search_result.identifier not in summary_row_map
    ]
    if not missing_summary_ids:
        return

    extra_summary_rows = _load_revalidated_summary_rows(
        db,
        limit=len(missing_summary_ids),
        summary_ids=[str(summary_id) for summary_id in missing_summary_ids],
    )
    for summary_row in extra_summary_rows:
        summary_row_map[str(summary_row["id"])] = {
            "id": summary_row["id"],
            "main_point": summary_row["main_point"],
            "source_record_ids": summary_row["source_record_ids"],
            "record_relations": summary_row["record_relations"],
            "created_at": summary_row["created_at"],
            "revalidation_status": summary_row["revalidation_status"],
            "effective_trust_level_score": summary_row["effective_trust_level_score"],
        }


def _build_ordered_summary_results(
    *,
    summary_rows: list[dict[str, Any]],
    summary_search_results: list[Any],
    summary_row_map: dict[str, dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Build the final ordered summary list from search-ranked and baseline rows."""
    ordered_reflections: list[dict[str, Any]] = []
    used_summary_ids: set[str] = set()
    for search_result in summary_search_results:
        summary_id = search_result.identifier
        summary_entry = summary_row_map.get(summary_id)
        if summary_entry is None or summary_id in used_summary_ids:
            continue
        ordered_reflections.append(summary_entry)
        used_summary_ids.add(summary_id)
    for summary_row in summary_rows:
        summary_id = str(summary_row["id"])
        if summary_id in used_summary_ids:
            continue
        ordered_reflections.append(
            {
                "id": summary_row["id"],
                "main_point": summary_row["main_point"],
                "source_record_ids": summary_row["source_record_ids"],
                "record_relations": summary_row["record_relations"],
                "created_at": summary_row["created_at"],
                "revalidation_status": summary_row["revalidation_status"],
                "effective_trust_level_score": summary_row[
                    "effective_trust_level_score"
                ],
            }
        )
    return ordered_reflections[:limit]


def _resolve_retrieved_messages(
    *,
    db: DatabaseManager,
    msg_search_results: list[Any],
    loaded_message_ids: set[str],
    role_filter: str | None,
    memory_query: bool,
) -> list[dict[str, Any]]:
    """Resolve message search hits into full rows after dedup and filtering."""
    if not msg_search_results:
        return []

    hit_ids = [
        search_result.identifier
        for search_result in msg_search_results
        if search_result.identifier not in loaded_message_ids
    ]
    if not hit_ids:
        return []

    rows = db.execute(
        _build_in_clause_query(
            "SELECT id, role, content, conversation_id, created_at "
            "FROM messages WHERE id IN",
            len(hit_ids),
        ),
        tuple(hit_ids),
    ).fetchall()
    row_map = {row[0]: row for row in rows}
    return _build_retrieved_message_results(
        hit_ids=hit_ids,
        row_map=row_map,
        role_filter=role_filter,
        memory_query=memory_query,
    )


def _build_retrieved_message_results(
    *,
    hit_ids: list[str],
    row_map: dict[str, tuple],
    role_filter: str | None,
    memory_query: bool,
) -> list[dict[str, Any]]:
    """Build the final retrieved-message payload from loaded message rows."""
    retrieved_messages: list[dict[str, Any]] = []
    for message_id in hit_ids:
        row = row_map.get(message_id)
        if row is None:
            continue
        if role_filter and row[1] != role_filter:
            continue
        if _should_skip_retrieved_message(
            raw_content=row[2],
            current_query_is_memory_query=memory_query,
        ):
            continue
        retrieved_messages.append(
            {
                "id": row[0],
                "role": row[1],
                "content": row[2],
                "conversation_id": row[3],
                "created_at": row[4],
            }
        )
    return retrieved_messages


def _resolve_context_search_plan(
    p: LoadContextParams,
) -> tuple[object, str | None, bool, int, int, int]:
    """Resolve routing and search-layer limits for one context query."""
    question_route = route_memory_question(p.query)
    role_filter = question_route.role_filter
    memory_query = question_route.memory_query
    (
        record_search_limit,
        summary_search_limit,
        message_search_limit,
    ) = _memory_weighted_search_limits(
        record_limit=p.record_limit,
        summary_limit=p.summary_limit,
        message_search_limit=p.message_search_limit,
        memory_query=memory_query,
        role_filter=role_filter,
    )
    return (
        question_route,
        role_filter,
        memory_query,
        record_search_limit,
        summary_search_limit,
        message_search_limit,
    )


def _serialize_message_content(
    content: str | list[dict[str, Any]],
) -> tuple[str, str]:
    """Serialize one stored message payload and extract plain text for FTS."""
    if isinstance(content, list):
        return (
            json.dumps(content),
            " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ),
        )
    return content, content


def _resolve_next_conversation_title(
    *,
    db: DatabaseManager,
    conversation_id: str,
    role: str,
    content: str | list[dict[str, Any]],
) -> str:
    """Resolve the conversation title after saving one new message."""
    existing_conversation = db.execute(
        "SELECT title FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    current_title = str(existing_conversation[0]) if existing_conversation else ""
    next_title = current_title.strip()
    if next_title or role != "user" or not isinstance(content, str):
        return next_title

    collapsed_content = " ".join(content.split()).strip()
    if collapsed_content:
        return collapsed_content[:80]
    return next_title


def make_save_message_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_message operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Save a message to the database."""
        p = _validate_params(params, SaveMessageParams)
        message_id = str(uuid.uuid4())
        db_content, text_for_index = _serialize_message_content(p.content)
        next_title = _resolve_next_conversation_title(
            db=db,
            conversation_id=p.conversation_id,
            role=p.role,
            content=p.content,
        )
        db.execute(
            "INSERT OR IGNORE INTO conversations (id, title) VALUES (?, ?)",
            (p.conversation_id, next_title),
        )
        cursor = db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, channel, meta) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, p.conversation_id, p.role, db_content, p.channel, p.meta),
        )
        db.execute(
            "UPDATE conversations "
            "SET title = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (next_title, p.conversation_id),
        )
        db.commit()
        index_message(db, cursor.lastrowid, text_for_index)
        db.commit()
        return {"message_id": message_id}

    return handle


def make_load_recent_messages_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for load_recent_messages operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load recent messages for a conversation."""
        p = _validate_params(params, LoadRecentMessagesParams)
        rows = db.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (p.conversation_id, p.limit),
        ).fetchall()
        return {
            "messages": [
                {"role": row[0], "content": parse_content(row[1])} for row in rows
            ]
        }

    return handle


def make_fulltext_search_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for fulltext_search operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Search messages or records via FTS5."""
        from backend.memory.retrieval.fulltext import sanitize_fts_query

        p = _validate_params(params, FulltextSearchParams)
        safe_query = sanitize_fts_query(p.query)
        if not safe_query:
            return {"results": []}
        if p.table == "messages":
            rows = db.execute(
                "SELECT rowid, snippet(messages_fts, 0, '', '', '...', 32) "
                "FROM messages_fts WHERE messages_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (safe_query, p.limit),
            ).fetchall()
        elif p.table == "records":
            rows = db.execute(
                "SELECT rowid, snippet(records_fts, 0, '', '', '...', 32) "
                "FROM records_fts WHERE records_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (safe_query, p.limit),
            ).fetchall()
        elif p.table == "summaries":
            rows = db.execute(
                "SELECT rowid, snippet(summaries_fts, 0, '', '', '...', 32) "
                "FROM summaries_fts WHERE summaries_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (safe_query, p.limit),
            ).fetchall()
        else:
            raise ValueError(f"Invalid search table: '{p.table}'")
        return {"results": [{"rowid": r[0], "snippet": r[1]} for r in rows]}

    return handle


def make_load_context_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for load_context operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load combined context: summaries, records, messages.

        When query and query_embedding are provided, runs hybrid
        search and includes ranked results for context prioritization.
        """
        p = _validate_params(params, LoadContextParams)
        summary_rows, records, messages = _load_base_context_rows(
            db,
            p,
        )
        loaded_message_ids = {m[0] for m in messages}
        result = _build_base_context_result(
            summary_rows=summary_rows,
            records=records,
            messages=messages,
        )
        result["conversation_summary"] = _load_conversation_summary(
            db,
            p.conversation_id,
        )
        _log_base_context_load(
            session_id=p.session_id,
            query=p.query,
            records=records,
            messages=messages,
            summary_rows=summary_rows,
        )

        if p.query is not None:
            from backend.memory.retrieval import hybrid_search

            (
                _question_route,
                role_filter,
                memory_query,
                record_search_limit,
                summary_search_limit,
                message_search_limit,
            ) = _resolve_context_search_plan(
                p,
            )

            search_results = _run_record_context_search(
                db=db,
                query=p.query,
                query_embedding=p.query_embedding,
                limit=record_search_limit,
            )
            result["search_results"] = _serialize_search_results(search_results)

            _append_missing_record_search_hits(
                db=db,
                result=result,
                records=records,
                search_results=search_results,
            )

            if search_results:
                logger.debug(
                    "hybrid_search query=%r → %d results (top: %s %.4f)",
                    p.query[:50],
                    len(search_results),
                    search_results[0].source,
                    search_results[0].score,
                )

            msg_search_results = hybrid_search(
                db,
                p.query,
                None,
                "messages",
                limit=message_search_limit,
            )
            result["message_search_results"] = _serialize_search_results(
                msg_search_results,
            )

            summary_search_results = hybrid_search(
                db,
                p.query,
                None,
                "summaries",
                limit=summary_search_limit,
            )
            result["summary_search_results"] = _serialize_search_results(
                summary_search_results,
            )

            summary_row_map = _build_summary_row_map(summary_rows)
            _extend_summary_row_map_with_search_hits(
                db=db,
                summary_row_map=summary_row_map,
                summary_search_results=summary_search_results,
            )
            result["summaries"] = _build_ordered_summary_results(
                summary_rows=summary_rows,
                summary_search_results=summary_search_results,
                summary_row_map=summary_row_map,
                limit=p.summary_limit,
            )

            retrieved_messages = _resolve_retrieved_messages(
                db=db,
                msg_search_results=msg_search_results,
                loaded_message_ids=loaded_message_ids,
                role_filter=role_filter,
                memory_query=memory_query,
            )
            result["retrieved_messages"] = retrieved_messages

            if msg_search_results:
                logger.debug(
                    "message_search query=%r memory_query=%s → %d hits, %d retrieved "
                    "(after dedup)",
                    p.query[:50],
                    memory_query,
                    len(msg_search_results),
                    len(retrieved_messages),
                )

        return result

    return handle


def make_save_conversation_summary_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for save_conversation_summary operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist the S-zone conversation summary for one dialog."""
        p = _validate_params(params, SaveConversationSummaryParams)
        db.execute(
            "UPDATE conversations SET conversation_summary = ? WHERE id = ?",
            (p.summary, p.conversation_id),
        )
        db.commit()
        return {"saved": True}

    return handle
