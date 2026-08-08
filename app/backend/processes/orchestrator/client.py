"""Typed async client for DB Proxy named operations.

Wraps an IPCSocketClient and provides typed methods for each
database operation used by the Orchestrator process. Domain-specific
methods are implemented in companion modules (client_memory_ops,
client_diagnostic_ops, client_conversation_ops) and delegated here.
"""

# ruff: noqa: ASYNC109

import asyncio
import uuid
from typing import Any

from backend.ipc.ipc_channel import IPCChannelError, IPCSocketClient
from backend.ipc.models import DBOperation, DBResult
from backend.processes.orchestrator import client_conversation_ops as conv_ops
from backend.processes.orchestrator import client_diagnostic_ops as diag_ops
from backend.processes.orchestrator import client_memory_ops as mem_ops


# --- Constants ---
DB_OPERATION_TIMEOUT = 30.0
DB_TYPE_MAP = {"DBResult": DBResult}


class DBProxyError(Exception):
    """Raised when a DB Proxy operation fails."""


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
        )

    def _build_ipc_error(
        self,
        *,
        operation: str,
        error: IPCChannelError,
    ) -> DBProxyError:
        """Build the IPC transport error for one DB proxy operation."""
        return DBProxyError(f"DB Proxy IPC error for '{operation}': {error}")

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
    ) -> str:
        """Save a message to the database."""
        return await conv_ops.save_message(
            self,
            conversation_id,
            role,
            content,
            channel,
            meta,
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
        summary_limit: int = 10,
        record_limit: int = 20,
        message_limit: int = 50,
        query: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        """Load combined context: summaries, records, messages."""
        return await conv_ops.load_context(
            self,
            session_id,
            conversation_id,
            summary_limit,
            record_limit,
            message_limit,
            query,
            query_embedding,
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
        """Append an audit log entry.

        Args:
            event_type: Audit event type.
            tool_name: Tool name.
            parameters: JSON parameters (redacted).
            result: Result summary.
            zone: Trust zone.
            duration_ms: Execution time.

        Returns:
            Entry ID.
        """
        return await self._send_int_operation(
            "write_audit",
            result_key="entry_id",
            parameters={
                "event_type": event_type,
                "tool_name": tool_name,
                "parameters": parameters,
                "result": result,
                "zone": zone,
                "duration_ms": duration_ms,
            },
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
        """Verify audit log hash chain integrity.

        Returns:
            True if chain is valid.
        """
        return await self._send_bool_operation(
            "verify_chain",
            result_key="valid",
        )

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

    async def migrate_summaries_to_assertions(self) -> dict[str, object]:
        """Run the idempotent legacy-summaries migration."""
        return await mem_ops.migrate_summaries_to_assertions(self)

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

    async def store_embedding(
        self,
        record_id: str,
        embedding: list[float],
    ) -> bool:
        """Store an embedding vector for an record."""
        return await mem_ops.store_embedding(self, record_id, embedding)

    async def fetch_untainted_records(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch untainted records for distiller."""
        return await mem_ops.fetch_untainted_records(self, limit)

    async def count_distillations(self) -> int:
        """Count total summaries in the database."""
        return await mem_ops.count_distillations(self)

    # --- Summary operations (delegated to client_memory_ops) ---

    async def save_summary(
        self,
        main_point: str,
        based_on: list[str] | None = None,
        trust_level: str = "",
        summary_scope: str = "long_term",
        source_record_ids: list[str] | None = None,
        record_relations: list[dict[str, str]] | None = None,
    ) -> str:
        """Save a summary."""
        return await mem_ops.save_summary(
            self,
            main_point,
            based_on,
            trust_level,
            summary_scope,
            source_record_ids,
            record_relations,
        )

    async def merge_summary_sources(
        self,
        summary_id: str,
        main_point: str = "",
        based_on: list[str] | None = None,
        trust_level: str = "",
        summary_scope: str = "long_term",
        trust_level_score: int | None = None,
        source_record_ids: list[str] | None = None,
        record_relations: list[dict[str, str]] | None = None,
    ) -> bool:
        """Attach new source records to an existing summary."""
        return await mem_ops.merge_summary_sources(
            self,
            summary_id,
            main_point,
            based_on,
            trust_level,
            summary_scope,
            trust_level_score,
            source_record_ids,
            record_relations,
        )

    async def list_summaries(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List summaries."""
        return await mem_ops.list_summaries(self, limit, offset)

    async def list_records(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List records with full metadata."""
        return await mem_ops.list_records(self, limit, offset)

    async def list_summaries_full(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List summaries with source record metadata."""
        return await mem_ops.list_summaries_full(self, limit, offset)

    async def count_memory_entries(self) -> dict[str, int]:
        """Return counts for messages, records, and summaries."""
        return await mem_ops.count_memory_entries(self)

    # --- Scheduled tasks ---

    async def create_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a new scheduled task."""
        return await self.send_operation("create_scheduled_task", params)

    async def list_scheduled_tasks(self, status: str = "all") -> list[dict[str, Any]]:
        """List scheduled tasks."""
        return await self._send_list_operation(
            "list_scheduled_tasks",
            result_key="tasks",
            parameters={"status": status},
        )

    async def get_due_tasks(self, now: str) -> list[dict[str, Any]]:
        """Get tasks that are due for execution."""
        return await self._send_list_operation(
            "get_due_tasks",
            result_key="tasks",
            parameters={"now": now},
        )

    async def advance_task_next_run(
        self,
        task_id: str,
        next_run_at: str,
        repeat_count: int,
    ) -> dict[str, Any]:
        """Advance next_run_at before execution."""
        return await self.send_operation(
            "advance_task_next_run",
            {
                "task_id": task_id,
                "next_run_at": next_run_at,
                "repeat_count": repeat_count,
            },
        )

    async def mark_task_run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Mark a task run as completed or failed."""
        return await self.send_operation("mark_task_run", params)

    async def update_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update a scheduled task."""
        return await self.send_operation("update_scheduled_task", params)

    async def delete_scheduled_task(self, task_id: str) -> dict[str, Any]:
        """Delete a scheduled task."""
        return await self.send_operation("delete_scheduled_task", {"task_id": task_id})

    async def record_task_run(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a single task run in the run history table."""
        return await self.send_operation("record_task_run", params)

    async def list_task_runs(
        self,
        task_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent runs for a task."""
        return await self._send_list_operation(
            "list_task_runs",
            result_key="runs",
            parameters={"task_id": task_id, "limit": limit},
        )

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
        """Create or update a skill.

        Args:
            name: Kebab-case unique identifier.
            title: Human-readable name.
            description: One-line summary.
            trigger: When to use this skill.
            procedure: JSON array of procedure steps.
            errors: JSON array of error/fix pairs.
            verification: How to verify the result.
            tool_sequence: Comma-separated tool names.

        Returns:
            Confirmation dict with skill name and status.
        """
        return await self.send_operation(
            "skill_create",
            {
                "name": name,
                "title": title,
                "description": description,
                "trigger": trigger,
                "procedure": procedure,
                "errors": errors,
                "verification": verification,
                "tool_sequence": tool_sequence,
            },
        )

    async def search_skills(
        self,
        query: str,
    ) -> list[dict[str, Any]]:
        """Search skills via FTS5.

        Args:
            query: Search query string.

        Returns:
            List of matching skill dicts with full content.
        """
        return await self._send_list_operation(
            "skill_search",
            result_key="skills",
            parameters={"query": query},
        )

    async def increment_skill_use(
        self,
        skill_id: str,
    ) -> dict[str, Any]:
        """Increment use_count for a skill.

        Args:
            skill_id: Skill UUID.

        Returns:
            Status dict.
        """
        return await self.send_operation(
            "skill_use",
            {"skill_id": skill_id},
        )

    async def list_skills(self) -> list[dict[str, Any]]:
        """List all skills as a compact index.

        Returns:
            List of skill dicts with name, title, description, use_count.
        """
        return await self._send_list_operation(
            "skill_list",
            result_key="skills",
            parameters={},
        )

    async def log_skill_usage(
        self,
        skill_id: str,
        question: str,
        status: str = "success",
        duration_ms: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        """Log a skill usage entry.

        Args:
            skill_id: Skill UUID.
            question: User question text.
            status: Result status (success/partial/error).
            duration_ms: Response duration.
            note: Optional note.

        Returns:
            Log entry ID.
        """
        return await self.send_operation(
            "skill_usage_log_create",
            {
                "skill_id": skill_id,
                "question": question,
                "status": status,
                "duration_ms": duration_ms,
                "note": note,
            },
        )

    async def delete_skill(self, name: str) -> dict[str, Any]:
        """Delete a skill by name.

        Args:
            name: Skill name to delete.

        Returns:
            Status dict.
        """
        return await self.send_operation(
            "skill_delete",
            {"name": name},
        )

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
        result = await self.send_operation("list_project_roots")
        return result.get("project_roots", [])

    async def add_project_root(
        self,
        *,
        name: str,
        root_path: str,
    ) -> dict[str, Any] | None:
        """Create or update one trusted project root."""
        result = await self.send_operation(
            "add_project_root",
            {"name": name, "root_path": root_path},
        )
        return result.get("project_root")

    async def remove_project_root(self, root_id: str) -> bool:
        """Remove one trusted project root."""
        result = await self.send_operation("remove_project_root", {"id": root_id})
        return bool(result.get("removed"))

    async def list_capability_grants(
        self,
        *,
        scope_type: str | None = None,
        scope_value: str | None = None,
        capability: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted assistant action grants."""
        params = {
            key: value
            for key, value in {
                "scope_type": scope_type,
                "scope_value": scope_value,
                "capability": capability,
                "channel": channel,
            }.items()
            if value is not None
        }
        result = await self.send_operation("list_capability_grants", params)
        return result.get("grants", [])

    async def create_capability_grant(
        self,
        *,
        scope_type: str,
        scope_value: str,
        capability: str,
        decision: str,
        channel: str = "",
        expires_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist one assistant action grant."""
        result = await self.send_operation(
            "create_capability_grant",
            {
                "scope_type": scope_type,
                "scope_value": scope_value,
                "capability": capability,
                "decision": decision,
                "channel": channel,
                "expires_at": expires_at,
            },
        )
        return result.get("grant")

    async def lookup_capability_grant(
        self,
        *,
        scope_type: str,
        scope_value: str,
        capability: str,
        channel: str = "",
    ) -> dict[str, Any] | None:
        """Return the best persisted grant for one action scope."""
        result = await self.send_operation(
            "lookup_capability_grant",
            {
                "scope_type": scope_type,
                "scope_value": scope_value,
                "capability": capability,
                "channel": channel,
            },
        )
        return result.get("grant")

    async def revoke_capability_grant(self, grant_id: str) -> bool:
        """Remove one persisted assistant action grant."""
        result = await self.send_operation("revoke_capability_grant", {"id": grant_id})
        return bool(result.get("revoked"))

    async def expire_capability_grants(self, now: str | None = None) -> int:
        """Delete expired assistant action grants."""
        params = {"now": now} if now is not None else {}
        result = await self.send_operation("expire_capability_grants", params)
        return int(result.get("expired", 0))

    async def create_agent_run(
        self,
        *,
        conversation_id: str,
        run_id: str | None = None,
        session_id: str = "",
        channel: str = "",
        request_id: str = "",
        status: str = "running",
        current_step: str = "",
        resume_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create a persisted assistant run."""
        result = await self.send_operation(
            "create_agent_run",
            {
                "conversation_id": conversation_id,
                "run_id": run_id,
                "session_id": session_id,
                "channel": channel,
                "request_id": request_id,
                "status": status,
                "current_step": current_step,
                "resume_payload": resume_payload or {},
            },
        )
        return result.get("agent_run")

    async def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        """Return one persisted assistant run."""
        result = await self.send_operation("get_agent_run", {"run_id": run_id})
        return result.get("agent_run")

    async def create_agent_run_event(
        self,
        *,
        run_id: str,
        conversation_id: str,
        event_type: str,
        request_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Append one runtime event for an assistant run."""
        result = await self.send_operation(
            "create_agent_run_event",
            {
                "run_id": run_id,
                "request_id": request_id,
                "conversation_id": conversation_id,
                "event_type": event_type,
                "payload": payload or {},
            },
        )
        return result.get("event")

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
        result = await self.send_operation(
            "list_agent_run_events",
            {
                "run_id": run_id,
                "request_id": request_id,
                "conversation_id": conversation_id,
                "after_sequence": after_sequence,
                "limit": limit,
            },
        )
        events = result.get("events", [])
        return events if isinstance(events, list) else []

    async def update_agent_run_status(
        self,
        *,
        run_id: str,
        status: str,
        current_step: str = "",
        resume_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update one persisted assistant run status."""
        params: dict[str, Any] = {
            "run_id": run_id,
            "status": status,
            "current_step": current_step,
        }
        if resume_payload is not None:
            params["resume_payload"] = resume_payload
        result = await self.send_operation("update_agent_run_status", params)
        return result.get("agent_run")

    async def create_pending_action(
        self,
        *,
        run_id: str,
        tool_name: str,
        action_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        channel: str = "",
        status: str = "waiting",
        preview: dict[str, Any] | None = None,
        resume_snapshot: dict[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a persisted pending action for one assistant run."""
        result = await self.send_operation(
            "create_pending_action",
            {
                "run_id": run_id,
                "action_id": action_id,
                "tool_name": tool_name,
                "parameters": parameters or {},
                "channel": channel,
                "status": status,
                "preview": preview or {},
                "resume_snapshot": resume_snapshot or {},
                "expires_at": expires_at,
            },
        )
        return result.get("pending_action")

    async def get_pending_action(self, action_id: str) -> dict[str, Any] | None:
        """Return one persisted pending action."""
        result = await self.send_operation(
            "get_pending_action",
            {"action_id": action_id},
        )
        return result.get("pending_action")

    async def get_resume_snapshot(
        self,
        *,
        action_id: str = "",
        snapshot_id: str = "",
    ) -> dict[str, Any] | None:
        """Return continuation state for one persisted pending action."""
        result = await self.send_operation(
            "get_resume_snapshot",
            {"action_id": action_id, "snapshot_id": snapshot_id},
        )
        return result.get("resume_snapshot")

    async def list_pending_actions(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted pending actions with optional filters."""
        params = {
            key: value
            for key, value in {
                "run_id": run_id,
                "status": status,
                "channel": channel,
            }.items()
            if value is not None
        }
        result = await self.send_operation("list_pending_actions", params)
        return result.get("pending_actions", [])

    async def decide_pending_action(
        self,
        *,
        action_id: str,
        decision: str,
        channel: str = "",
    ) -> dict[str, Any]:
        """Persist an approve/deny decision for one pending action."""
        return await self.send_operation(
            "decide_pending_action",
            {"action_id": action_id, "decision": decision, "channel": channel},
        )

    async def complete_pending_action(
        self,
        *,
        action_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str = "",
    ) -> dict[str, Any]:
        """Persist the execution result for one approved pending action."""
        return await self.send_operation(
            "complete_pending_action",
            {
                "action_id": action_id,
                "status": status,
                "result": result or {},
                "error_message": error_message,
            },
        )

    async def expire_pending_actions(self, now: str | None = None) -> int:
        """Mark expired pending actions as expired."""
        params = {"now": now} if now is not None else {}
        result = await self.send_operation("expire_pending_actions", params)
        return int(result.get("expired", 0))

    async def prune_runtime_state(
        self,
        *,
        now: str | None = None,
        snapshot_retention_hours: int = 24,
        run_retention_days: int = 7,
    ) -> dict[str, int]:
        """Expire and delete stale short-lived assistant runtime rows."""
        params: dict[str, Any] = {
            "snapshot_retention_hours": snapshot_retention_hours,
            "run_retention_days": run_retention_days,
        }
        if now is not None:
            params["now"] = now
        result = await self.send_operation("prune_runtime_state", params)
        return {
            "expired_pending_actions": int(result.get("expired_pending_actions", 0)),
            "deleted_resume_snapshots": int(result.get("deleted_resume_snapshots", 0)),
            "deleted_agent_runs": int(result.get("deleted_agent_runs", 0)),
        }

    # --- Admin operations ---

    async def get_stats(self) -> dict[str, int]:
        """Get database statistics.

        Returns:
            Dict with table name -> row count.
        """
        return await self.send_operation("get_stats")

    # --- Integration config operations ---

    async def get_integration_config(
        self,
        service_type: str,
    ) -> dict[str, Any]:
        """Return integration + all credentials for a service_type.

        Args:
            service_type: Integration type (e.g. 'openrouter', 'ollama', 'telegram').

        Returns:
            Dict with 'integration' (or None) and 'credentials' keys.
        """
        return await self.send_operation(
            "get_integration_config",
            {
                "service_type": service_type,
            },
        )

    async def record_rate_limit_call(
        self,
        tool_name: str,
        called_at: float,
    ) -> dict[str, Any]:
        """Persist a rate limit call timestamp.

        Args:
            tool_name: Name of the tool that was called.
            called_at: Unix timestamp of the call.

        Returns:
            Dict with 'ok' key.
        """
        return await self.send_operation(
            "record_rate_limit_call",
            {
                "tool_name": tool_name,
                "called_at": called_at,
            },
        )

    async def get_recent_rate_limit_calls(
        self,
        tool_name: str,
        since: float,
    ) -> dict[str, Any]:
        """Return recent rate limit call timestamps for a tool.

        Args:
            tool_name: Name of the tool.
            since: Unix timestamp; return calls after this time.

        Returns:
            Dict with 'timestamps' list.
        """
        return await self.send_operation(
            "get_recent_rate_limit_calls",
            {
                "tool_name": tool_name,
                "since": since,
            },
        )

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


