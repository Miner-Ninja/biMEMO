"""Factory functions for orchestrator callbacks.

Creates async callables (context_fn, save_fn, save_record_fn,
audit_fn, reminder handlers, system_status handler) that delegate
to the DB Proxy via IPC.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from .client import DBProxyClient, DBProxyError


logger = logging.getLogger(__name__)
RECOVERABLE_EMBEDDING_ERRORS = (
    RuntimeError,
    ValueError,
    DBProxyError,
    httpx.HTTPError,
)
LOAD_CONTEXT_COMPRESSION_SESSION_ID = "s-zone-compression"


def _meta_json(meta: dict | None) -> str | None:
    """Serialize optional message metadata for DB proxy storage."""
    if not meta:
        return None
    return json.dumps(meta)


def _route_section_value(
    route_section: dict[str, object] | None,
    key: str,
) -> object:
    """Read one raw field from an optional route section."""
    if not route_section:
        return None
    return route_section.get(key)


def _optional_route_string(
    route_section: dict[str, object] | None,
    key: str,
) -> str | None:
    """Read one optional string field from an optional route section."""
    value = _route_section_value(route_section, key)
    return str(value) if value is not None else None


def _route_signal_groups(route_section: dict[str, object] | None) -> list[str]:
    """Normalize matched signal groups for one optional route section."""
    groups = _route_section_value(route_section, "matched_signal_groups") or []
    return [str(group) for group in groups]


def _normalized_route_section(
    route_trace: dict[str, object],
    key: str,
) -> dict[str, object] | None:
    """Load one optional normalized route section from a route trace."""
    route_data = route_trace.get(key)
    if not isinstance(route_data, dict):
        return None
    return dict(route_data)


def _summary_row_tuple(summary_row: object) -> object:
    """Normalize one summary row from DB proxy output."""
    if not isinstance(summary_row, dict):
        return summary_row
    return (
        summary_row.get("id", ""),
        summary_row.get("main_point", ""),
        (
            json.loads(summary_row["source_record_ids"])
            if isinstance(summary_row.get("source_record_ids"), str)
            else summary_row.get("source_record_ids", [])
        ),
        summary_row.get("created_at", ""),
        summary_row.get("revalidation_status", ""),
        summary_row.get("effective_trust_level_score", 0),
        summary_row.get("record_relations", []),
        summary_row.get("scope", "long_term"),
    )


def _record_row_tuple(record: dict[str, object]) -> tuple[object, ...]:
    """Normalize one record row from DB proxy output."""
    return (
        record["id"],
        record["summary"],
        record.get("key_facts", []),
        record.get("action_items", []),
        record.get("created_at", ""),
        record.get("source_message_ids", []),
        record.get("tainted", False),
    )


def _message_row_tuple(message: dict[str, object]) -> tuple[object, object]:
    """Normalize one message row from DB proxy output."""
    return (message["role"], message["content"])


def make_context_fn(
    db_client: DBProxyClient,
    *,
    llm_context_window: int,
    embed_fn: Callable | None = None,
    user_tz: ZoneInfo | None = None,
) -> Callable:
    """Create an async context_fn for the orchestrator.

    Loads summaries, records, and messages via DB Proxy IPC.
    When a query is provided, runs hybrid search to rank records
    by relevance before assembling context.

    Args:
        db_client: Connected DBProxyClient.
        llm_context_window: Active LLM context window size in tokens.
            Required: the silent fallback to a default constant has been
            removed. Resolve via the capabilities registry before calling.
        embed_fn: Optional async callable(text) -> list[float] for
            generating query embeddings. If None, vector search is skipped.
        user_tz: User's timezone for converting UTC timestamps in
            memory entries. If None, timestamps stay as UTC.

    Returns:
        Async callable(session_id, query=None) -> list of ContextEntry.
    """
    from backend.memory.classification.question_classifier import is_memory_query
    from backend.memory.context_builder import (
        ContextResult,
        build_context_with_ids,
    )
    from backend.memory.retrieval import SearchResult

    async def _maybe_embed_query(query: str | None) -> list[float] | None:
        """Generate an embedding for the query when embedding is available."""
        if not query or embed_fn is None:
            return None
        try:
            return await embed_fn(query)
        except RECOVERABLE_EMBEDDING_ERRORS:
            logger.debug("Embedding generation failed, using keyword-only search")
            return None

    def _context_load_params(
        session_id: str,
        conversation_id: str,
        query: str | None,
        query_embedding: list[float] | None,
    ) -> dict[str, object]:
        """Build DB proxy load_context parameters for one query."""
        load_params: dict[str, object] = {
            "session_id": session_id,
            "conversation_id": conversation_id,
        }
        if query:
            load_params["query"] = query
            if query_embedding is not None:
                load_params["query_embedding"] = query_embedding
        return load_params

    def _search_results(
        raw_results: list[dict[str, object]] | None,
    ) -> list[SearchResult] | None:
        """Convert DB proxy search result dicts back into SearchResult objects."""
        if raw_results is None:
            return None
        return [
            SearchResult(
                identifier=result["identifier"],
                source=result["source"],
                score=result["score"],
            )
            for result in raw_results
        ]

    def _retrieved_messages(
        raw_messages: list[dict[str, object]] | None,
    ) -> list[tuple[object, ...]] | None:
        """Normalize retrieved messages for context builder input."""
        if not raw_messages:
            return None
        return [
            (
                message["id"],
                message["role"],
                message["content"],
                message["conversation_id"],
                message.get("created_at", ""),
            )
            for message in raw_messages
        ]

    def _context_builder_inputs(
        data: dict[str, object],
    ) -> tuple[
        list[object],
        list[tuple[object, ...]],
        list[tuple[object, object]],
        list[SearchResult] | None,
        list[tuple[object, ...]] | None,
        list[SearchResult] | None,
    ]:
        """Normalize DB proxy context payload into context-builder inputs."""
        summaries = [_summary_row_tuple(summary) for summary in data["summaries"]]
        records = [_record_row_tuple(record) for record in data["records"]]
        messages = [_message_row_tuple(message) for message in data["messages"]]
        search_results = _search_results(data.get("search_results"))
        retrieved_messages = _retrieved_messages(data.get("retrieved_messages"))
        summary_search_results = _search_results(data.get("summary_search_results"))
        return (
            summaries,
            records,
            messages,
            search_results,
            retrieved_messages,
            summary_search_results,
        )

    async def context_fn(
        session_id: str,
        query: str | None = None,
        conversation_id: str = "",
    ) -> ContextResult:
        """Build LLM context via DB Proxy IPC.

        Args:
            session_id: Current session identifier.
            query: Optional user query for search-ranked context.
            conversation_id: Conversation identifier for raw message history.

        Returns:
            ContextResult with entries and retrieved memory summaries.
        """
        memory_query = is_memory_query(query) if query else False
        query_embedding = await _maybe_embed_query(query)
        data = await db_client.load_context(
            **_context_load_params(
                session_id,
                conversation_id,
                query,
                query_embedding,
            )
        )
        (
            summaries,
            records,
            messages,
            search_results,
            retrieved_messages,
            summary_search_results,
        ) = _context_builder_inputs(data)

        return build_context_with_ids(
            summaries,
            records,
            messages,
            search_results=search_results,
            llm_context_window=llm_context_window,
            retrieved_messages=retrieved_messages,
            summary_search_results=summary_search_results,
            memory_query=memory_query,
            session_id=session_id,
            user_tz=user_tz,
        )

    return context_fn


def make_zone_context_fn(
    db_client: DBProxyClient,
    capabilities_resolver: Callable[[], Awaitable[object]] | None = None,
    compression_llm_call: Callable | None = None,
) -> Callable:
    """Create an async zone-based context_fn for the orchestrator.

    Loads recent messages and persisted S-zone summary from the DB.
    When messages are evicted from W, they are returned in the result
    for background compression. The compressed summary is persisted
    and loaded on the next turn.

    Args:
        db_client: Connected DBProxyClient.
        capabilities_resolver: Async callable returning the resolved
            ``ModelCapabilities`` for the chat session's primary model.
            Required at chat time. When ``None`` is passed, the resulting
            ``zone_context_fn`` will raise on first invocation — this
            tolerates the placeholder built during early orchestrator
            construction, where the LLM client does not yet exist.
        compression_llm_call: Optional async callable for S-zone
            LLM compression. When None, background task uses
            primitive extraction.

    Returns:
        Async callable(session_id, query, conversation_id) -> ContextResult.
    """
    from backend.memory.context_builder import ContextResult
    from backend.memory.context_builder.models import ContextZoneBudget
    from backend.memory.context_builder.zone_builder import (
        build_zone_context,
        compress_evicted_with_llm,
        compress_messages_to_summary,
    )

    compression_locks: dict[str, asyncio.Lock] = {}
    compression_tasks: set[asyncio.Task[None]] = set()

    def _compression_lock(conversation_id: str) -> asyncio.Lock:
        """Return the shared per-conversation compression lock."""
        lock = compression_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            compression_locks[conversation_id] = lock
        return lock

    async def zone_context_fn(
        session_id: str,
        query: str | None = None,
        conversation_id: str = "",
    ) -> ContextResult:
        """Build zone-based LLM context with only recent messages.

        Loads the persisted S-zone summary from DB. If eviction
        occurs, schedules background compression — S-zone update
        appears on the next turn.

        Args:
            session_id: Current session identifier.
            query: Unused (kept for interface compatibility).
            conversation_id: Conversation identifier.

        Returns:
            ContextResult with zone-budgeted entries.
        """
        del query
        if capabilities_resolver is None:
            raise RuntimeError(
                "make_zone_context_fn was constructed without a "
                "capabilities_resolver; cannot start chat. The orchestrator "
                "must replace the placeholder context_fn after _init_llm.",
            )
        capabilities = await capabilities_resolver()
        data = await db_client.load_context(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        raw_messages = data.get("messages") or []
        messages = [_message_row_tuple(m) for m in raw_messages]
        prev_summary = str(data.get("conversation_summary") or "")

        budget = ContextZoneBudget.from_context_window(
            num_ctx=capabilities.max_context_tokens,
        )
        result = build_zone_context(
            recent_messages=messages,
            conversation_summary=prev_summary,
            zone_budget=budget,
        )

        if result.evicted_messages:
            _schedule_background_compression(
                conversation_id,
                result.evicted_messages,
                prev_summary,
            )

        return result

    def _schedule_background_compression(
        conversation_id: str,
        evicted: list[tuple[str, str]],
        prev_summary: str,
    ) -> None:
        """Schedule S-zone compression as a background asyncio task."""
        compression_task = asyncio.create_task(
            _compress_and_persist(
                conversation_id,
                evicted,
                prev_summary,
            ),
            name=f"s-zone-compress:{conversation_id}",
        )
        compression_tasks.add(compression_task)
        compression_task.add_done_callback(compression_tasks.discard)

    async def _compress_and_persist(
        conversation_id: str,
        evicted: list[tuple[str, str]],
        prev_summary: str,
    ) -> None:
        """Compress evicted messages and persist the summary to DB."""
        try:
            async with _compression_lock(conversation_id):
                latest_summary = await _load_latest_summary(
                    conversation_id,
                    prev_summary,
                )
                new_summary = await _compress_evicted(
                    evicted,
                    latest_summary,
                )
                if new_summary:
                    await _persist_summary(
                        conversation_id,
                        new_summary,
                    )
        except Exception:
            logger.warning(
                "Background S-zone compression failed for %s",
                conversation_id,
                exc_info=True,
            )

    async def _load_latest_summary(
        conversation_id: str,
        prev_summary: str,
    ) -> str:
        """Reload the persisted summary before appending more evicted content."""
        try:
            data = await db_client.load_context(
                session_id=LOAD_CONTEXT_COMPRESSION_SESSION_ID,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.warning(
                "Failed to reload latest S-zone summary for %s",
                conversation_id,
                exc_info=True,
            )
            return prev_summary
        return str(data.get("conversation_summary") or prev_summary)

    async def _compress_evicted(
        evicted: list[tuple[str, str]],
        prev_summary: str,
    ) -> str:
        """Compress evicted messages via LLM or primitive fallback."""
        if compression_llm_call is not None:
            return await compress_evicted_with_llm(
                evicted,
                prev_summary,
                compression_llm_call,
            )
        if prev_summary:
            new_part = compress_messages_to_summary(evicted)
            if new_part:
                return prev_summary + "\n" + new_part
            return prev_summary
        return compress_messages_to_summary(evicted)

    async def _persist_summary(
        conversation_id: str,
        summary: str,
    ) -> None:
        """Save updated S-zone summary to the database."""
        try:
            await db_client.save_conversation_summary(
                conversation_id,
                summary,
            )
        except Exception:
            logger.warning(
                "Failed to persist S-zone summary for %s",
                conversation_id,
                exc_info=True,
            )

    return zone_context_fn


def make_evidence_bundle_fn(db_client: DBProxyClient) -> Callable:
    """Create an async evidence bundle loader for narrow source-backed recall."""
    from backend.memory.evidence_chain import load_evidence_bundle_for_query

    async def evidence_bundle_fn(query_text: str) -> object | None:
        """Load one evidence bundle for the current user question."""
        return await load_evidence_bundle_for_query(query_text, db_client)

    return evidence_bundle_fn


def make_save_fn(db_client: DBProxyClient) -> Callable:
    """Create an async save_fn for the orchestrator.

    Saves messages via DB Proxy IPC.

    Args:
        db_client: Connected DBProxyClient.

    Returns:
        Async callable(conversation_id, role, content, meta) -> str.
    """

    async def save_fn(
        conversation_id: str,
        role: str,
        content: str,
        meta: dict | None = None,
    ) -> str:
        """Save a message via DB Proxy IPC.

        Args:
            conversation_id: Conversation ID.
            role: Message role.
            content: Message text.
            meta: Optional metadata dict (JSON-serialized before storage).
        """
        return await db_client.save_message(
            conversation_id,
            role,
            content,
            meta=_meta_json(meta),
        )

    return save_fn


def make_save_record_fn(db_client: DBProxyClient) -> Callable:
    """Create an async save_record_fn for the orchestrator.

    Saves user messages as records via DB Proxy IPC,
    making them searchable through FTS.

    Args:
        db_client: Connected DBProxyClient.

    Returns:
        Async callable(
            conversation_id,
            session_id,
            summary,
            source_message_ids,
        ) -> None.
    """

    async def save_record_fn(
        conversation_id: str,
        session_id: str,
        summary: str,
        source_message_ids: list[str] | None = None,
    ) -> None:
        """Save a user message as an record via DB Proxy IPC.

        Args:
            conversation_id: Conversation ID.
            session_id: Session ID.
            summary: User message text to store as record.
            source_message_ids: Message IDs used to form the record.
        """
        await db_client.save_record(
            conversation_id=conversation_id,
            session_id=session_id,
            summary=summary,
            source_message_ids=source_message_ids,
        )

    return save_record_fn


def make_audit_fn(db_client: DBProxyClient) -> Callable:
    """Create an async audit_fn for the tool broker.

    Sends audit entries via DB Proxy IPC instead of writing
    directly to the database.

    Args:
        db_client: Connected DBProxyClient.

    Returns:
        Async callable matching the ToolBroker audit interface.
    """

    async def audit_fn(
        event_type: str,
        tool_name: str,
        parameters: str | None = None,
        result: str | None = None,
        zone: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Write audit entry via DB Proxy IPC.

        Args:
            event_type: Audit event type.
            tool_name: Tool name.
            parameters: Redacted parameters JSON.
            result: Result summary.
            zone: Trust zone.
            duration_ms: Execution time.
        """
        try:
            await db_client.write_audit(
                event_type=event_type,
                tool_name=tool_name,
                parameters=parameters,
                result=result,
                zone=zone,
                duration_ms=duration_ms,
            )
        except DBProxyError:
            logger.warning(
                "Failed to write audit for %s/%s",
                event_type,
                tool_name,
                exc_info=True,
            )

    return audit_fn


def make_memory_route_diagnostic_fn(db_client: DBProxyClient) -> Callable:
    """Create an async writer for structured memory-route diagnostics."""

    def _memory_route_diagnostic_payload(
        *,
        session_id: str,
        conversation_id: str,
        message_id: str,
        channel: str,
        route_trace: dict[str, object],
        used_long_term_mode: bool,
    ) -> dict[str, object]:
        """Build the DB proxy payload for one route-diagnostic snapshot."""
        initial_route = _normalized_route_section(route_trace, "initial_route")
        final_route = _normalized_route_section(route_trace, "final_route")
        fallback_route = _normalized_route_section(route_trace, "fallback_route")
        return {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "channel": channel,
            "initial_intent": str(
                (initial_route or {}).get("intent", "non_memory_question")
            ),
            "initial_confidence": str((initial_route or {}).get("confidence", "none")),
            "initial_role_filter": _optional_route_string(
                initial_route,
                "role_filter",
            ),
            "initial_source": str((initial_route or {}).get("source", "signal")),
            "initial_signal_groups": _route_signal_groups(initial_route),
            "fallback_attempted": bool(route_trace.get("fallback_attempted", False)),
            "fallback_reason": (
                str(route_trace.get("fallback_reason"))
                if route_trace.get("fallback_reason") is not None
                else None
            ),
            "fallback_intent": (_optional_route_string(fallback_route, "intent")),
            "fallback_confidence": (
                _optional_route_string(fallback_route, "confidence")
            ),
            "fallback_role_filter": (
                _optional_route_string(fallback_route, "role_filter")
            ),
            "fallback_source": (_optional_route_string(fallback_route, "source")),
            "final_intent": str(
                (final_route or {}).get("intent", "non_memory_question")
            ),
            "final_confidence": str((final_route or {}).get("confidence", "none")),
            "final_role_filter": _optional_route_string(final_route, "role_filter"),
            "final_source": str((final_route or {}).get("source", "signal")),
            "final_signal_groups": _route_signal_groups(final_route),
            "route_mismatch": bool(route_trace.get("route_mismatch", False)),
            "mismatch_label": str(route_trace.get("mismatch_label", "no_mismatch")),
            "used_long_term_mode": used_long_term_mode,
        }

    async def write_memory_route_diagnostic_fn(
        *,
        session_id: str,
        conversation_id: str,
        message_id: str,
        channel: str,
        route_trace: dict[str, object],
        used_long_term_mode: bool,
    ) -> None:
        """Persist one route-diagnostic snapshot via DB Proxy IPC."""
        try:
            await db_client.write_memory_route_diagnostic(
                **_memory_route_diagnostic_payload(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    channel=channel,
                    route_trace=route_trace,
                    used_long_term_mode=used_long_term_mode,
                )
            )
        except DBProxyError:
            logger.warning(
                "Failed to write memory-route diagnostic",
                exc_info=True,
            )

    return write_memory_route_diagnostic_fn
