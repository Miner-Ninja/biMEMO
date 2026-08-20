"""Typed async client for DB Proxy named operations.

Wraps an IPCSocketClient and provides typed methods for each
database operation used by the Orchestrator process. Domain-specific
methods are implemented in companion ``client_*_ops`` modules and
delegated here.
"""

# ruff: noqa: ASYNC109

import asyncio
import uuid
from typing import Any

from backend.ipc.errors import (
    DBPROXY_ERROR_CODE,
    DBPROXY_IPC_CODE,
    DBPROXY_TIMEOUT_CODE,
)
from backend.ipc.ipc_channel import IPCChannelError, IPCSocketClient
from backend.ipc.models import DBOperation, DBResult
from backend.processes.orchestrator import client_admin_ops as admin_ops
from backend.processes.orchestrator import client_agent_turn_ops as turn_ops
from backend.processes.orchestrator import client_audit_ops as audit_ops
from backend.processes.orchestrator import client_conversation_ops as conv_ops
from backend.processes.orchestrator import client_diagnostic_ops as diag_ops
from backend.processes.orchestrator import client_memory_ops as mem_ops
from backend.processes.orchestrator import client_permission_ops as perm_ops
from backend.processes.orchestrator import client_scheduling_ops as sched_ops
from backend.processes.orchestrator import client_skills_ops as skills_ops


# --- Constants ---
DB_OPERATION_TIMEOUT = 30.0
DB_TYPE_MAP = {"DBResult": DBResult}


class DBProxyError(Exception):
    """Raised when a DB Proxy operation fails.

    Attributes:
        code: Stable machine-readable failure code carried over IPC
            (defaults to ``dbproxy_error`` when the server sent none).
    """

    def __init__(self, message: str, code: str = DBPROXY_ERROR_CODE) -> None:
        """Initialize the error with its message and code."""
        super().__init__(message)
        self.code = code


class DBProxyClient:
    """Typed async client for DB Proxy named operations.

    Wraps an IPCSocketClient and provides typed methods for each
    database operation. All calls go through send_operation which
    constructs a DBOperation, sends it over IPC, and awaits DBResult.

    Attributes:
        _ipc: Underlying IPC socket client.
        _timeout: Default timeout for operations in seconds.
    """

    def __init__(
        self,
        ipc_client: IPCSocketClient,
        timeout: float = DB_OPERATION_TIMEOUT,
    ) -> None:
        """Initialize the DB Proxy client wrapper.

        Args:
            ipc_client: Connected IPCSocketClient to DB Proxy.
            timeout: Default timeout for operations in seconds.
        """
        self._ipc = ipc_client
        self._timeout = timeout

    async def send_operation(
        self,
        operation: str,
        parameters: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a named operation to DB Proxy and await the result.

        Args:
            operation: Operation name (e.g., "save_message").
            parameters: Operation parameters dict.
            timeout: Override timeout for this call.

        Returns:
            Result data dict from DB Proxy.

        Raises:
            DBProxyError: If the operation fails or times out.
        """
        effective_timeout = timeout or self._timeout
        operation_request = self._build_operation_request(
            operation=operation,
            parameters=parameters,
        )
        try:
            response = await self._send_operation_request(
                operation_request=operation_request,
                timeout=effective_timeout,
            )
        except TimeoutError as error:
            raise self._build_timeout_error(
                operation=operation,
                timeout=effective_timeout,
            ) from error
        except IPCChannelError as error:
            raise self._build_ipc_error(operation=operation, error=error) from error

        return self._extract_operation_result(
            operation=operation,
            response=response,
        )

    async def _send_list_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """Send one operation and return a list field from the response."""
        result = await self.send_operation(operation, parameters, timeout)
        list_result = self._result_field(result, result_key, [])
        if isinstance(list_result, list):
            return list_result
        return []

    async def _send_optional_dict_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Send one operation and return an optional dict field."""
        result = await self.send_operation(operation, parameters, timeout)
        dict_result = self._result_field(result, result_key)
        if isinstance(dict_result, dict):
            return dict_result
        return None

    async def _send_identifier_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> str:
        """Send one operation and return a string identifier field."""
        result = await self.send_operation(operation, parameters, timeout)
        if result_key not in result:
            raise DBProxyError(
                f"DB Proxy operation '{operation}' returned no '{result_key}' field",
            )
        return str(result[result_key])

    async def _send_int_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> int:
        """Send one operation and return an integer field."""
        result = await self.send_operation(operation, parameters, timeout)
        return int(result[result_key])

    async def _send_bool_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> bool:
        """Send one operation and return a boolean-ish field."""
        result = await self.send_operation(operation, parameters, timeout)
        return bool(result.get(result_key))

    def _build_operation_request(
        self,
        *,
        operation: str,
        parameters: dict[str, Any] | None,
    ) -> DBOperation:
        """Build one DB operation request envelope."""
        return DBOperation(
            request_id=str(uuid.uuid4()),
            operation=operation,
            parameters=parameters,
        )

    @staticmethod
    def _result_field(
        result: dict[str, Any],
        key: str,
        default: object = None,
    ) -> object:
        """Return one field from a DB proxy result payload."""
        return result.get(key, default)

    async def _send_operation_request(
        self,
        *,
        operation_request: DBOperation,
        timeout: float,
    ) -> DBResult | object:
        """Send one DB operation envelope through IPC."""
        send_and_receive = getattr(
            self._ipc,
            "send_and_receive_matching",
            self._ipc.send_and_receive,
        )
        return await asyncio.wait_for(
            send_and_receive(operation_request),
            timeout=timeout,
        )

    def _build_timeout_error(
        self,
        *,
        operation: str,
        timeout: float,
    ) -> DBProxyError:
        """Build the timeout error for one DB proxy operation."""
        return DBProxyError(
            f"DB Proxy operation '{operation}' timed out after {timeout:.1f}s",
            code=DBPROXY_TIMEOUT_CODE,
        )

    def _build_ipc_error(
        self,
        *,
        operation: str,
        error: IPCChannelError,
    ) -> DBProxyError:
        """Build the IPC transport error for one DB proxy operation."""
        return DBProxyError(
            f"DB Proxy IPC error for '{operation}': {error}",
            code=DBPROXY_IPC_CODE,
        )

    def _extract_operation_result(
        self,
        *,
        operation: str,
        response: DBResult | object,
    ) -> dict[str, Any]:
        """Validate one DB proxy response and return its data payload."""
        if not isinstance(response, DBResult):
            raise DBProxyError(
                f"Unexpected response type: {type(response).__name__}",
            )
        if response.status == "error":
            raise DBProxyError(
                f"DB Proxy operation '{operation}' failed: {response.error_message}",
                code=response.error_code or DBPROXY_ERROR_CODE,
            )
        return response.data or {}

    # --- Message operations (delegated to client_conversation_ops) ---

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        channel: str | None = None,
        meta: str | None = None,
        client_message_id: str | None = None,
    ) -> str:
        """Save a message to the database."""
        return await conv_ops.save_message(
            self,
            conversation_id,
            role,
            content,
            channel,
            meta,
            client_message_id=client_message_id,
        )

    async def save_conversation_summary(
        self,
        conversation_id: str,
        summary: str,
    ) -> dict[str, Any]:
        """Persist the S-zone conversation summary for one dialog."""
        return await self.send_operation(
            "save_conversation_summary",
            {
                "conversation_id": conversation_id,
                "summary": summary,
            },
        )

    async def load_recent_messages(
        self,
        conversation_id: str,
        limit: int = 50,
    ) -> list[dict[str, str]]:
        """Load recent messages for a conversation."""
        return await conv_ops.load_recent_messages(self, conversation_id, limit)

    async def fulltext_search(
        self,
        query: str,
        table: str = "messages",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search messages or records via FTS5."""
        return await conv_ops.fulltext_search(self, query, table, limit)

    async def list_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List stored messages with optional conversation filtering."""
        return await conv_ops.list_messages(self, limit, offset, conversation_id)

    # --- Context operations (delegated to client_conversation_ops) ---

    async def load_context(
        self,
        session_id: str,
        conversation_id: str = "",
        record_limit: int = 20,
        message_limit: int = 50,
    ) -> dict[str, Any]:
        """Load baseline context: records, messages, S-zone summary."""
        return await conv_ops.load_context(
            self,
            session_id,
            conversation_id,
            record_limit,
            message_limit,
        )

    # --- Audit operations ---

    async def write_audit(
        self,
        event_type: str,
        tool_name: str | None = None,
        parameters: str | None = None,
        result: str | None = None,
        zone: str | None = None,
        duration_ms: float | None = None,
    ) -> int:
        """Append an audit log entry."""
        return await audit_ops.write_audit(
            self,
            event_type,
            tool_name,
            parameters,
            result,
            zone,
            duration_ms,
        )

    async def write_memory_route_diagnostic(
        self,
        *,
        session_id: str,
        conversation_id: str,
        message_id: str,
        channel: str,
        initial_intent: str,
        initial_confidence: str,
        initial_role_filter: str | None,
        initial_source: str,
        initial_signal_groups: list[str],
        fallback_attempted: bool,
        fallback_reason: str | None,
        fallback_intent: str | None,
        fallback_confidence: str | None,
        fallback_role_filter: str | None,
        fallback_source: str | None,
        final_intent: str,
        final_confidence: str,
        final_role_filter: str | None,
        final_source: str,
        final_signal_groups: list[str],
        route_mismatch: bool,
        mismatch_label: str,
        used_long_term_mode: bool,
    ) -> int:
        """Persist one structured memory-route diagnostic record."""
        return await diag_ops.write_memory_route_diagnostic(
            self,
            session_id=session_id,
            conversation_id=conversation_id,
            message_id=message_id,
            channel=channel,
            initial_intent=initial_intent,
            initial_confidence=initial_confidence,
            initial_role_filter=initial_role_filter,
            initial_source=initial_source,
            initial_signal_groups=initial_signal_groups,
            fallback_attempted=fallback_attempted,
            fallback_reason=fallback_reason,
            fallback_intent=fallback_intent,
            fallback_confidence=fallback_confidence,
            fallback_role_filter=fallback_role_filter,
            fallback_source=fallback_source,
            final_intent=final_intent,
            final_confidence=final_confidence,
            final_role_filter=final_role_filter,
            final_source=final_source,
            final_signal_groups=final_signal_groups,
            route_mismatch=route_mismatch,
            mismatch_label=mismatch_label,
            used_long_term_mode=used_long_term_mode,
        )

    async def write_record_processing_diagnostic(
        self,
        *,
        decision: str,
        conversation_id: str,
        session_id: str,
        record_id: str = "",
        model: str = "",
        message_count: int = 0,
        source_message_ids: list[str] | None = None,
        messages: list[str] | None = None,
        injection_flagged: bool = False,
        summary: str = "",
        key_facts: list[str] | None = None,
        action_items: list[str] | None = None,
        memory_kinds: list[str] | None = None,
        prompt: list[dict[str, Any]] | None = None,
        raw_output: str = "",
        error: str = "",
        detail: str = "",
    ) -> int:
        """Persist one structured record-processing diagnostic record."""
        return await diag_ops.write_record_processing_diagnostic(
            self,
            decision=decision,
            conversation_id=conversation_id,
            session_id=session_id,
            record_id=record_id,
            model=model,
            message_count=message_count,
            source_message_ids=source_message_ids,
            messages=messages,
            injection_flagged=injection_flagged,
            summary=summary,
            key_facts=key_facts,
            action_items=action_items,
            memory_kinds=memory_kinds,
            prompt=prompt,
            raw_output=raw_output,
            error=error,
            detail=detail,
        )

    async def write_summary_processing_diagnostic(
        self,
        *,
        decision: str,
        summary_id: str = "",
        model: str = "",
        record_count: int = 0,
        source_record_ids: list[str] | None = None,
        records: list[dict[str, Any]] | None = None,
        prompt: list[dict[str, Any]] | None = None,
        raw_output: str = "",
        main_point: str = "",
        based_on: list[str] | None = None,
        trust_level: str = "",
        scope: str = "",
        record_relations: list[str] | None = None,
        original_scope: str = "",
        scope_adjustment_reason: str = "",
        error: str = "",
        detail: str = "",
    ) -> int:
        """Persist one structured summary-processing diagnostic record."""
        return await diag_ops.write_summary_processing_diagnostic(
            self,
            decision=decision,
            summary_id=summary_id,
            model=model,
            record_count=record_count,
            source_record_ids=source_record_ids,
            records=records,
            prompt=prompt,
            raw_output=raw_output,
            main_point=main_point,
            based_on=based_on,
            trust_level=trust_level,
            scope=scope,
            record_relations=record_relations,
            original_scope=original_scope,
            scope_adjustment_reason=scope_adjustment_reason,
            error=error,
            detail=detail,
        )

    async def verify_chain(self) -> bool:
        """Verify audit log hash chain integrity."""
        return await audit_ops.verify_chain(self)

    # --- Record operations (delegated to client_memory_ops) ---

    async def save_record(
        self,
        conversation_id: str,
        session_id: str,
        summary: str,
        key_facts: list[str] | None = None,
        action_items: list[str] | None = None,
        memory_kinds: list[str] | None = None,
        source_message_ids: list[str] | None = None,
        tainted: bool = False,
    ) -> str:
        """Save an record."""
        return await mem_ops.save_record(
            self,
            conversation_id,
            session_id,
            summary,
            key_facts,
            action_items,
            memory_kinds,
            source_message_ids,
            tainted,
        )

    async def save_queued_record(
        self,
        conversation_id: str,
        session_id: str,
        messages: list[str],
        source_message_ids: list[str] | None = None,
        injection_flagged: bool = False,
    ) -> str:
        """Persist one pending dialog-recorder queue item."""
        return await mem_ops.save_queued_record(
            self,
            conversation_id,
            session_id,
            messages,
            source_message_ids,
            injection_flagged,
        )

    async def delete_queued_records(self, queue_ids: list[str]) -> None:
        """Delete processed dialog-recorder queue rows."""
        await mem_ops.delete_queued_records(self, queue_ids)

    async def list_queued_records(
        self,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        """List pending dialog-recorder queue rows, oldest first."""
        return await mem_ops.list_queued_records(self, limit)

    async def bump_queued_record_attempts(self, queue_ids: list[str]) -> None:
        """Increment the attempt counter on pending queue rows."""
        await mem_ops.bump_queued_record_attempts(self, queue_ids)

    # --- Graph operations (delegated to client_memory_ops) ---

    async def save_entity(
        self,
        entity_type: str,
        canonical_name: str,
        aliases: list[str] | None = None,
    ) -> str:
        """Get or create one graph entity; returns its id."""
        return await mem_ops.save_entity(
            self,
            entity_type,
            canonical_name,
            aliases,
        )

    async def save_assertion(
        self,
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
        return await mem_ops.save_assertion(
            self,
            subject_entity_id=subject_entity_id,
            relation=relation,
            assertion_text=assertion_text,
            object_entity_id=object_entity_id,
            object_value=object_value,
            trust_level=trust_level,
            assertion_scope=assertion_scope,
            valid_from=valid_from,
            source_record_ids=source_record_ids,
        )

    async def invalidate_assertion(
        self,
        assertion_id: str,
        invalidated_by_assertion_id: str | None = None,
    ) -> bool:
        """Mark one assertion as no longer valid."""
        return await mem_ops.invalidate_assertion(
            self,
            assertion_id,
            invalidated_by_assertion_id,
        )

    async def get_entity(self, entity_id: str) -> dict[str, object] | None:
        """Load one entity by id."""
        return await mem_ops.get_entity(self, entity_id)

    async def find_entities(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Find entities by full-text query."""
        return await mem_ops.find_entities(self, query, limit)

    async def list_assertions_for_entity(
        self,
        entity_id: str,
        valid_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """List assertions where the entity is subject or object."""
        return await mem_ops.list_assertions_for_entity(
            self,
            entity_id,
            valid_only,
            limit,
        )

    async def find_graph_entry_points(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Find labeled entity/assertion entry points for a query."""
        return await mem_ops.find_graph_entry_points(self, query, limit)

    async def fetch_unextracted_records(
        self,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Fetch untainted records not yet extracted into the graph."""
        return await mem_ops.fetch_unextracted_records(self, limit)

    async def prune_graph(self) -> dict[str, object]:
        """Run one graph maintenance pass."""
        return await mem_ops.prune_graph(self)

    async def prune_unlinked_records(
        self,
        age_days: int,
    ) -> dict[str, object]:
        """Delete old records that no assertion cites as evidence."""
        return await mem_ops.prune_unlinked_records(self, age_days)

    async def record_loop_heartbeat(
        self,
        loop_name: str,
    ) -> dict[str, object]:
        """Stamp a successful pass of one background loop."""
        return await mem_ops.record_loop_heartbeat(self, loop_name)

    async def get_graph_stats(self) -> dict[str, object]:
        """Return the graph statistics snapshot."""
        return await mem_ops.get_graph_stats(self)

    async def store_embedding(
        self,
        record_id: str,
        embedding: list[float],
    ) -> bool:
        """Store an embedding vector for an record."""
        return await mem_ops.store_embedding(self, record_id, embedding)

    async def vector_search(
        self,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return records nearest to the query embedding."""
        return await mem_ops.vector_search(self, query_embedding, limit)

    # --- Summary operations (delegated to client_memory_ops) ---

    async def list_records(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List records with full metadata."""
        return await mem_ops.list_records(self, limit, offset)

    async def count_memory_entries(self) -> dict[str, int]:
        """Return counts for messages, records, and summaries."""
        return await mem_ops.count_memory_entries(self)

    # --- Scheduled tasks ---

    async def activate_tool_registry(self, revisions: dict[str, str]) -> dict[str, Any]:
        """Activate the finalized broker projection at the DB write boundary."""
        return await self.send_operation(
            "activate_tool_registry",
            {"revisions": revisions},
        )

    async def create_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a new scheduled task."""
        return await sched_ops.create_scheduled_task(self, params)

    async def list_scheduled_tasks(self, status: str = "all") -> list[dict[str, Any]]:
        """List scheduled tasks."""
        return await sched_ops.list_scheduled_tasks(self, status)

    async def get_due_tasks(self, now: str) -> list[dict[str, Any]]:
        """Get tasks that are due for execution."""
        return await sched_ops.get_due_tasks(self, now)

    async def advance_task_next_run(
        self,
        task_id: str,
        next_run_at: str,
        repeat_count: int,
    ) -> dict[str, Any]:
        """Advance next_run_at before execution."""
        return await sched_ops.advance_task_next_run(
            self,
            task_id,
            next_run_at,
            repeat_count,
        )

    async def mark_task_run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Mark a task run as completed or failed."""
        return await sched_ops.mark_task_run(self, params)

    async def update_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update a scheduled task."""
        return await sched_ops.update_scheduled_task(self, params)

    async def delete_scheduled_task(self, task_id: str) -> dict[str, Any]:
        """Delete a scheduled task."""
        return await sched_ops.delete_scheduled_task(self, task_id)

    async def record_task_run(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a single task run in the run history table."""
        return await sched_ops.record_task_run(self, params)

    async def list_task_runs(
        self,
        task_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent runs for a task."""
        return await sched_ops.list_task_runs(self, task_id, limit)

    # --- Skill operations ---

    async def create_skill(
        self,
        name: str,
        title: str,
        description: str,
        trigger: str = "",
        procedure: str = "[]",
        errors: str = "[]",
        verification: str = "",
        tool_sequence: str = "",
    ) -> dict[str, Any]:
        """Create or update a skill."""
        return await skills_ops.create_skill(
            self,
            name,
            title,
            description,
            trigger,
            procedure,
            errors,
            verification,
            tool_sequence,
        )

    async def search_skills(
        self,
        query: str,
    ) -> list[dict[str, Any]]:
        """Search skills via FTS5."""
        return await skills_ops.search_skills(self, query)

    async def increment_skill_use(
        self,
        skill_id: str,
    ) -> dict[str, Any]:
        """Increment use_count for a skill."""
        return await skills_ops.increment_skill_use(self, skill_id)

    async def list_skills(self) -> list[dict[str, Any]]:
        """List all skills as a compact index."""
        return await skills_ops.list_skills(self)

    async def log_skill_usage(
        self,
        skill_id: str,
        question: str,
        status: str = "success",
        duration_ms: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        """Log a skill usage entry."""
        return await skills_ops.log_skill_usage(
            self,
            skill_id,
            question,
            status,
            duration_ms,
            note,
        )

    async def delete_skill(self, name: str) -> dict[str, Any]:
        """Delete a skill by name."""
        return await skills_ops.delete_skill(self, name)

    # --- Conversation operations (delegated to client_conversation_ops) ---

    async def create_conversation(self, title: str = "") -> str:
        """Create a new conversation."""
        return await conv_ops.create_conversation(self, title)

    async def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List conversations."""
        return await conv_ops.list_conversations(self, limit, offset)

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation and cascade related data."""
        await conv_ops.delete_conversation(self, conversation_id)

    async def update_conversation_title(
        self,
        conversation_id: str,
        title: str,
    ) -> dict[str, str] | None:
        """Update one conversation title and return the stored row."""
        return await conv_ops.update_conversation_title(
            self,
            conversation_id,
            title,
        )

    async def update_conversation_processing_state(
        self,
        conversation_id: str,
        processing_status: str,
        processing_stage: str = "",
        processing_started_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Update one conversation processing state and return the stored row."""
        return await conv_ops.update_conversation_processing_state(
            self,
            conversation_id,
            processing_status,
            processing_stage,
            processing_started_at,
        )

    async def reset_stuck_processing_states(self) -> int:
        """Reset all stuck processing rows at orchestrator startup."""
        return await conv_ops.reset_stuck_processing_states(self)

    # --- User profile operations ---

    async def get_user_profile(self) -> dict[str, str]:
        """Get user profile from the database.

        Returns:
            Profile dict with display_name, timezone, language,
            custom_instructions keys (all optional strings).
        """
        result = await self.send_operation("get_user_profile")
        return result.get("profile", {})

    # --- Assistant permission operations ---

    async def list_project_roots(self) -> list[dict[str, Any]]:
        """Return configured project roots for assistant actions."""
        return await perm_ops.list_project_roots(self)

    async def add_project_root(
        self,
        *,
        name: str,
        root_path: str,
    ) -> dict[str, Any] | None:
        """Create or update one trusted project root."""
        return await perm_ops.add_project_root(self, name=name, root_path=root_path)

    async def remove_project_root(self, root_id: str) -> bool:
        """Remove one trusted project root."""
        return await perm_ops.remove_project_root(self, root_id)

    async def list_capability_grants(
        self,
        *,
        scope_type: str | None = None,
        scope_value: str | None = None,
        capability: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted assistant action grants."""
        return await perm_ops.list_capability_grants(
            self,
            scope_type=scope_type,
            scope_value=scope_value,
            capability=capability,
            channel=channel,
        )

    async def create_capability_grant(
        self,
        *,
        scope_type: str,
        scope_value: str,
        capability: str,
        decision: str,
        policy_revision: str,
        channel: str = "",
        scope_json: dict[str, Any] | None = None,
        scope_hash: str = "",
        origin_confirmation_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist one assistant action grant."""
        return await perm_ops.create_capability_grant(
            self,
            scope_type=scope_type,
            scope_value=scope_value,
            capability=capability,
            decision=decision,
            policy_revision=policy_revision,
            channel=channel,
            scope_json=scope_json,
            scope_hash=scope_hash,
            origin_confirmation_id=origin_confirmation_id,
            expires_at=expires_at,
        )

    async def lookup_capability_grant(
        self,
        *,
        scope_type: str,
        scope_value: str,
        capability: str,
        policy_revision: str,
        channel: str = "",
        scope_hash: str = "",
    ) -> dict[str, Any] | None:
        """Return the best persisted grant for one action scope."""
        return await perm_ops.lookup_capability_grant(
            self,
            scope_type=scope_type,
            scope_value=scope_value,
            capability=capability,
            policy_revision=policy_revision,
            channel=channel,
            scope_hash=scope_hash,
        )

    async def authorize_agent_invocation(
        self,
        *,
        turn_id: str,
        invocation_id: str,
        idempotency_key: str,
        capability: str,
        channel: str,
        scope_type: str,
        scope_value: str,
        scope_json: dict[str, Any],
        scope_hash: str,
        policy_revision: str,
        canonical_args_hash: str,
        confirmation_id: str | None = None,
        create_reusable_grant: bool = False,
        grant_expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Atomically authorize and claim one structured invocation."""
        return await perm_ops.authorize_agent_invocation(
            self,
            turn_id=turn_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            capability=capability,
            channel=channel,
            scope_type=scope_type,
            scope_value=scope_value,
            scope_json=scope_json,
            scope_hash=scope_hash,
            policy_revision=policy_revision,
            canonical_args_hash=canonical_args_hash,
            confirmation_id=confirmation_id,
            create_reusable_grant=create_reusable_grant,
            grant_expires_at=grant_expires_at,
        )

    async def revoke_capability_grant(self, grant_id: str) -> bool:
        """Remove one persisted assistant action grant."""
        return await perm_ops.revoke_capability_grant(self, grant_id)

    async def expire_capability_grants(self, now: str | None = None) -> int:
        """Delete expired assistant action grants."""
        return await perm_ops.expire_capability_grants(self, now)

    async def list_agent_run_events(
        self,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        conversation_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return persisted runtime events for a run or conversation."""
        return await perm_ops.list_agent_run_events(
            self,
            run_id=run_id,
            request_id=request_id,
            conversation_id=conversation_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def list_agent_confirmations(
        self,
        turn_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return every confirmation of one turn, any status, current first."""
        return await perm_ops.list_agent_confirmations(self, turn_id, limit=limit)

    async def list_pending_actions(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted pending actions with optional filters."""
        return await perm_ops.list_pending_actions(
            self,
            run_id=run_id,
            status=status,
            channel=channel,
        )

    async def prune_runtime_state(
        self,
        *,
        now: str | None = None,
        snapshot_retention_hours: int = 24,
        run_retention_days: int = 7,
    ) -> dict[str, int]:
        """Expire and delete stale short-lived assistant runtime rows."""
        return await perm_ops.prune_runtime_state(
            self,
            now=now,
            snapshot_retention_hours=snapshot_retention_hours,
            run_retention_days=run_retention_days,
        )

    # --- Core-authority agent turn operations ---

    async def create_agent_turn(
        self,
        *,
        conversation_id: str,
        session_id: str,
        channel: str,
        turn_id: str | None = None,
        finalization_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or replay one architecture-pinned authoritative turn."""
        return await turn_ops.create_agent_turn(
            self,
            conversation_id=conversation_id,
            session_id=session_id,
            channel=channel,
            turn_id=turn_id,
            finalization_id=finalization_id,
        )

    async def get_agent_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Read canonical core turn state and its complete ledger."""
        return await turn_ops.get_agent_turn(self, turn_id)

    async def upsert_agent_turn_obligations(
        self,
        turn_id: str,
        obligations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Idempotently persist mandatory evidence records."""
        return await turn_ops.upsert_agent_turn_obligations(
            self,
            turn_id,
            obligations,
        )

    async def upsert_agent_tool_batch(
        self,
        *,
        turn_id: str,
        batch_id: str,
        assistant_message: dict[str, Any],
        transcript_snapshot: list[dict[str, Any]],
        invocations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist one complete native tool-call batch."""
        return await turn_ops.upsert_agent_tool_batch(
            self,
            turn_id=turn_id,
            batch_id=batch_id,
            assistant_message=assistant_message,
            transcript_snapshot=transcript_snapshot,
            invocations=invocations,
        )

    async def claim_agent_invocation(
        self,
        *,
        turn_id: str,
        invocation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Commit the durable execution claim before broker dispatch."""
        return await turn_ops.claim_agent_invocation(
            self,
            turn_id=turn_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
        )

    async def complete_agent_invocation(
        self,
        *,
        turn_id: str,
        invocation_id: str,
        claim_generation: int,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Record a generation-fenced result and compatible success binds."""
        return await turn_ops.complete_agent_invocation(
            self,
            turn_id=turn_id,
            invocation_id=invocation_id,
            claim_generation=claim_generation,
            status=status,
            result=result,
            error=error,
        )

    async def decide_agent_confirmation(
        self,
        confirmation_id: str,
        decision: str,
    ) -> dict[str, Any]:
        """CAS a canonical confirmation decision in core authority."""
        return await turn_ops.decide_agent_confirmation(
            self,
            confirmation_id,
            decision,
        )

    async def get_agent_confirmation(
        self,
        confirmation_id: str,
    ) -> dict[str, Any] | None:
        """Read one canonical confirmation without mutating it."""
        return await turn_ops.get_agent_confirmation(self, confirmation_id)

    async def list_pending_agent_confirmations(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        """Return pending canonical confirmations for one conversation."""
        return await turn_ops.list_pending_agent_confirmations(self, conversation_id)

    async def append_agent_turn_event(
        self,
        *,
        turn_id: str,
        event_type: str,
        phase: str = "",
        attempt: int = 0,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Append an authoritative transition/provider-attempt outbox event."""
        return await turn_ops.append_agent_turn_event(
            self,
            turn_id=turn_id,
            event_type=event_type,
            phase=phase,
            attempt=attempt,
            payload=payload,
            event_id=event_id,
        )

    async def cancel_agent_turn(
        self,
        turn_id: str,
        *,
        reason: str = "cancelled",
        timeout: bool = False,
        failure: bool = False,
        task_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve cancellation against the canonical run-state CAS domain."""
        return await turn_ops.cancel_agent_turn(
            self,
            turn_id,
            reason=reason,
            timeout=timeout,
            failure=failure,
            task_result=task_result,
        )

    async def commit_agent_turn_finalization(
        self,
        *,
        turn_id: str,
        finalization_id: str,
        outcome_kind: str,
        conversation_id: str,
        session_id: str,
        assistant_content: str,
        assistant_meta: dict[str, Any] | None,
        dialog_content: str,
        source_message_ids: list[str],
        outcome_payload: dict[str, Any],
        persist_chat: bool = True,
        task_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically commit interactive FINAL or FAILED persistence."""
        return await turn_ops.commit_agent_turn_finalization(
            self,
            turn_id=turn_id,
            finalization_id=finalization_id,
            outcome_kind=outcome_kind,
            conversation_id=conversation_id,
            session_id=session_id,
            assistant_content=assistant_content,
            assistant_meta=assistant_meta,
            dialog_content=dialog_content,
            source_message_ids=source_message_ids,
            outcome_payload=outcome_payload,
            persist_chat=persist_chat,
            task_result=task_result,
        )

    async def list_unprojected_agent_turn_events(
        self,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Read stable events awaiting optional runtime projection."""
        return await turn_ops.list_unprojected_agent_turn_events(self, limit)

    async def list_agent_turn_events(
        self,
        *,
        turn_id: str | None = None,
        conversation_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Read canonical core events for reconnect recovery."""
        return await turn_ops.list_agent_turn_events(
            self,
            turn_id=turn_id,
            conversation_id=conversation_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def project_agent_turn_event(self, event: dict[str, Any]) -> bool:
        """Idempotently write one core event into the runtime projection."""
        return await turn_ops.project_agent_turn_event(self, event)

    async def mark_agent_turn_event_projected(
        self,
        event_id: str,
        projection_version: int = 1,
    ) -> bool:
        """Acknowledge one idempotent runtime projection."""
        return await turn_ops.mark_agent_turn_event_projected(
            self,
            event_id,
            projection_version,
        )

    async def recover_agent_turns(self) -> int:
        """Fence invocation claims abandoned by a previous orchestrator."""
        return await turn_ops.recover_agent_turns(self)

    async def create_agent_clarification(
        self,
        *,
        turn_id: str,
        clarification_id: str,
        proposed_tool: str,
        reason: str,
        expires_at: str,
    ) -> dict[str, Any]:
        """Persist one backend-owned low-confidence proposal."""
        return await turn_ops.create_agent_clarification(
            self,
            turn_id=turn_id,
            clarification_id=clarification_id,
            proposed_tool=proposed_tool,
            reason=reason,
            expires_at=expires_at,
        )

    async def get_pending_agent_clarification(
        self, conversation_id: str
    ) -> dict[str, Any] | None:
        """Read the latest live proposal for one conversation."""
        return await turn_ops.get_pending_agent_clarification(self, conversation_id)

    async def decide_agent_clarification(
        self, clarification_id: str, decision: str
    ) -> dict[str, Any]:
        """Close a proposal with a classified decision."""
        return await turn_ops.decide_agent_clarification(
            self,
            clarification_id,
            decision,
        )

    async def create_agent_pending_input(
        self,
        *,
        turn_id: str,
        pending_input_id: str,
        question: str,
        suggestions: list[str],
        expected_input_schema: dict[str, object],
        transcript_snapshot: list[dict[str, object]],
        expires_at: str,
    ) -> dict[str, object]:
        """Persist one typed missing-input pause in core authority."""
        return await turn_ops.create_agent_pending_input(
            self,
            turn_id=turn_id,
            pending_input_id=pending_input_id,
            question=question,
            suggestions=suggestions,
            expected_input_schema=expected_input_schema,
            transcript_snapshot=transcript_snapshot,
            expires_at=expires_at,
        )

    async def get_agent_pending_input(
        self,
        *,
        pending_input_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, object] | None:
        """Read one typed missing-input state from core authority."""
        return await turn_ops.get_agent_pending_input(
            self,
            pending_input_id=pending_input_id,
            conversation_id=conversation_id,
        )

    async def reply_agent_pending_input(
        self,
        *,
        turn_id: str,
        pending_input_id: str,
        conversation_id: str,
        cas_version: int,
        value: object,
    ) -> dict[str, object]:
        """Validate and CAS one reply into the same structured run."""
        return await turn_ops.reply_agent_pending_input(
            self,
            turn_id=turn_id,
            pending_input_id=pending_input_id,
            conversation_id=conversation_id,
            cas_version=cas_version,
            value=value,
        )

    # --- Admin operations ---

    async def get_stats(self) -> dict[str, int]:
        """Get database statistics."""
        return await admin_ops.get_stats(self)

    # --- Integration config operations ---

    async def get_integration_config(
        self,
        service_type: str,
    ) -> dict[str, Any]:
        """Return integration + all credentials for a service_type."""
        return await admin_ops.get_integration_config(self, service_type)

    async def record_rate_limit_call(
        self,
        tool_name: str,
        called_at: float,
    ) -> dict[str, Any]:
        """Persist a rate limit call timestamp."""
        return await admin_ops.record_rate_limit_call(self, tool_name, called_at)

    async def get_recent_rate_limit_calls(
        self,
        tool_name: str,
        since: float,
    ) -> dict[str, Any]:
        """Return recent rate limit call timestamps for a tool."""
        return await admin_ops.get_recent_rate_limit_calls(self, tool_name, since)

    # --- Vector table management (delegated to client_memory_ops) ---

    async def init_vector_table(
        self,
        embedding_model: str,
        dimensions: int,
    ) -> dict[str, Any]:
        """Initialize or recreate the vector table for embeddings."""
        return await mem_ops.init_vector_table(
            self,
            embedding_model,
            dimensions,
        )

    async def fetch_unembedded_records(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch records that have no embedding yet."""
        return await mem_ops.fetch_unembedded_records(self, limit)
