"""Factory functions for orchestrator callbacks.

Creates async callables (context_fn, save_fn, save_record_fn,
audit_fn, reminder handlers, system_status handler) that delegate
to the DB Proxy via IPC.
"""

import asyncio
import json
import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from backend.ipc.models import (
    McpAskRequest,
    McpAskResponse,
    McpMemoryRememberRequest,
    McpMemoryRememberResponse,
)

from .client import DBProxyClient, DBProxyError


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.memory.context_builder import ContextResult


logger = logging.getLogger(__name__)
LOAD_CONTEXT_COMPRESSION_SESSION_ID = "s-zone-compression"

# ── Write guard constants (R7) ──
# Content-blind guards only: a size cap and a rate cap. There is deliberately
# NO content-level dedup — collapsing repeated writes would starve the
# distillation pipeline of the corroborating source material that memory
# reinforcement emerges from. Flood control is the rate cap's job; redundancy
# is judged downstream by the Summary Distiller, not at the write gate.
_MAX_CONTENT_BYTES = 8192
_WRITE_RATE_PER_MIN = 20
_WRITE_RATE_WINDOW_SECONDS = 60.0
# Namespaces our rows in the shared rate_limit_calls table (Socket A),
# separate from the LLM tool broker's entries.
_MCP_WRITE_TOOL_NAME = "mcp_memory_remember"


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


def _message_row_tuple(message: dict[str, object]) -> tuple[object, object]:
    """Normalize one message row from DB proxy output."""
    return (message["role"], message["content"])


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
        result = replace(
            result,
            zone_budget={
                "working_memory": budget.working_memory,
                "recall_results": budget.recall_results,
                "conversation_summary": budget.conversation_summary,
            },
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
    """Create an async evidence bundle loader for narrow source-backed recall.

    With graph memory enabled the bundle chains assertion → evidence
    records → source messages; otherwise the legacy summary chain runs.
    """
    from backend.memory.evidence_chain import load_evidence_bundle_for_query
    from backend.memory.graph_recall import load_graph_evidence_bundle
    from backend.security.policy import get_policy

    async def evidence_bundle_fn(query_text: str) -> object | None:
        """Load one evidence bundle for the current user question."""
        if get_policy().get_bool("graph_memory.enabled"):
            return await load_graph_evidence_bundle(db_client, query_text)
        return await load_evidence_bundle_for_query(query_text, db_client)

    return evidence_bundle_fn


def make_graph_facts_fn(db_client: DBProxyClient) -> Callable:
    """Create an async loader of the user's valid graph facts.

    Feeds the deterministic long-term-memory answer when graph memory is
    enabled; returns dated fact lines about the user entity.
    """
    from backend.memory.graph_recall import build_user_fact_lines

    async def graph_facts_fn() -> list[str]:
        """List the user's currently-valid graph facts."""
        return await build_user_fact_lines(db_client)

    return graph_facts_fn


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


# ── Write guard state (R7) ──
# The rate cap is DB-backed (rate_limit_calls table, Socket A), so it survives
# an orchestrator restart — an agent can't reset its budget by forcing one.
# The lock serializes the check→store→record sequence so two concurrent
# writes can't both slip past the limit before either is recorded.
_mcp_memory_remember_lock = asyncio.Lock()


def make_mcp_memory_remember_fn(
    db_client: DBProxyClient,
    save_record_fn: object | None = None,
) -> Callable:
    """Create the store-only handler for MCP external-agent memories.

    The write runs in the orchestrator process (Socket A), so the
    web-facing FastAPI process never writes memory content directly.
    Each agent has ONE persistent session (via resolve_agent_session)
    — no throwaway conversations per fact.

    When ``save_record_fn`` is provided, the handler also enqueues a one-way
    record so the stored fact enters the normal memory pipeline
    (records → summaries) without weakening the existing ``save_dialog_record``
    guard on assistant-only turns.

    Applies two content-blind guards — a content-size cap (8192 bytes) and a
    DB-backed write rate-limit (20/min, survives restart) — plus an
    injection screen on the raw content: flagged writes are rejected before
    anything reaches the messages table or the FTS index. There is no
    content dedup — repeated writes are legitimate reinforcement and must
    reach the distillation pipeline. Rejections return stored=false with a
    reason.

    Args:
        db_client: Connected DBProxyClient (Socket A).
        save_record_fn: Optional queue-backed record writer
            (orchestrator.save_record_fn) for one-way record enqueuing (row 8).
            When None, facts are stored but not queued for record processing.

    Returns:
        Async callable(McpMemoryRememberRequest) -> McpMemoryRememberResponse.
    """
    from backend.security.content_filter import filter_input

    from .agent_session import resolve_agent_session

    async def mcp_memory_remember_fn(
        request: McpMemoryRememberRequest,
    ) -> McpMemoryRememberResponse:
        """Persist one MCP memory as a message with write guards."""
        now = time.time()

        async with _mcp_memory_remember_lock:
            # R7.2 — content-size cap
            if len(request.content.encode("utf-8")) > _MAX_CONTENT_BYTES:
                return McpMemoryRememberResponse(
                    request_id=request.request_id,
                    stored=False,
                    reason=f"oversized: content exceeds {_MAX_CONTENT_BYTES} bytes",
                )

            # Injection screen on the raw agent content. Messages carry no
            # tainted column, so a flagged write must be rejected here —
            # otherwise it would land in messages and the FTS index
            # unfiltered and stay retrievable by every reader.
            if filter_input(request.content).injection_flagged:
                logger.warning(
                    "MCP remember rejected: injection-flagged content "
                    "from agent %s",
                    request.agent_id,
                )
                return McpMemoryRememberResponse(
                    request_id=request.request_id,
                    stored=False,
                    reason="injection_flagged",
                )

            # R7.3 — rate-limit via the shared DB limiter (Socket A). Count our
            # own recent calls in the trailing window; the table self-prunes.
            recent = await db_client.get_recent_rate_limit_calls(
                tool_name=_MCP_WRITE_TOOL_NAME,
                since=now - _WRITE_RATE_WINDOW_SECONDS,
            )
            if len(recent.get("timestamps", [])) >= _WRITE_RATE_PER_MIN:
                return McpMemoryRememberResponse(
                    request_id=request.request_id,
                    stored=False,
                    reason="rate_limited",
                )

            # R1 — resolve (or create) ONE session for this agent
            conversation_id, session_id = await resolve_agent_session(
                agent_id=request.agent_id,
                db_client=db_client,
            )

            # Store the message into the agent's persistent session
            message_id = await db_client.save_message(
                conversation_id,
                "user",
                request.content,
                channel=request.channel,
            )

            # R2 — enqueue a one-way record via the orchestrator's existing
            # queue-backed record writer. This does NOT weaken
            # save_dialog_record's guard (that only fires on assistant
            # response_text); it is a SEPARATE user-only record path.
            if save_record_fn is not None:
                await save_record_fn(
                    conversation_id,
                    session_id,
                    request.content,
                    [message_id],
                )

            await db_client.record_rate_limit_call(
                tool_name=_MCP_WRITE_TOOL_NAME,
                called_at=now,
            )

            return McpMemoryRememberResponse(
                request_id=request.request_id,
                message_id=message_id,
                conversation_id=conversation_id,
                stored=True,
            )

    return mcp_memory_remember_fn



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


def make_mcp_ask_fn(db_client: DBProxyClient) -> Callable:
    """Create the two-way ask handler for MCP external agents.

    Resolves the agent session (get-or-create), builds a ChatRequest,
    and pushes it through the normal orchestrator chat pipeline. The
    answer is returned as an McpAskResponse — never via outbound send.

    Args:
        db_client: Connected DBProxyClient (Socket A).

    Returns:
        Async callable(McpAskRequest) -> McpAskResponse.
    """
    from backend.ipc.models import ChatRequest, ChatResponse

    from .agent_session import resolve_agent_session

    # We need the orchestrator_fn reference — it's passed at construction time
    # via the IPC server. The factory captures it as a closure.
    _orchestrator_fn: Callable | None = None

    def set_orchestrator_fn(fn: Callable) -> None:
        """Bind the orchestrator callable after factory construction."""
        nonlocal _orchestrator_fn
        _orchestrator_fn = fn

    async def mcp_ask_fn(request: McpAskRequest) -> McpAskResponse:
        """Run one MCP ask through the normal agentic loop."""
        try:
            # R1 — resolve (or create) ONE session for this agent
            conversation_id, session_id = await resolve_agent_session(
                agent_id=request.agent_id,
                db_client=db_client,
            )

            # Build a ChatRequest and run it through the orchestrator
            chat_req = ChatRequest(
                request_id=request.request_id,
                message=request.question,
                channel="bridge:mcp",
                session_id=session_id,
                conversation_id=conversation_id,
            )

            # Call the orchestrator fn directly — it returns ChatResponse
            if _orchestrator_fn is None:
                return McpAskResponse(
                    request_id=request.request_id,
                    error="orchestrator not initialized",
                )
            result = _orchestrator_fn(chat_req)
            if asyncio.iscoroutine(result):
                result = await result

            if isinstance(result, ChatResponse):
                answer = getattr(
                    result.structured_content,
                    "answer",
                    "",
                ) if result.structured_content else ""
                return McpAskResponse(
                    request_id=request.request_id,
                    answer=answer,
                    conversation_id=conversation_id,
                    session_id=session_id,
                )

            return McpAskResponse(
                request_id=request.request_id,
                error="unexpected orchestrator response",
            )
        except Exception as error:
            logger.error("MCP ask error: %s", error, exc_info=True)
            return McpAskResponse(
                request_id=request.request_id,
                error=str(error)[:200],
            )

    # Attach the setter so process.py can wire it after construction
    mcp_ask_fn.set_orchestrator_fn = set_orchestrator_fn  # type: ignore[attr-defined]
    return mcp_ask_fn
