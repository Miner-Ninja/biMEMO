"""Admin database client for DB Proxy Socket B operations.

Provides typed async methods for admin operations through the DB Proxy
IPC channel: signing material retrieval, conversation management,
audit logging, memory diagnostics, bridge links, file metadata,
and database statistics.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.processes.fastapi_app.base_client import _BaseIPCClient


logger = logging.getLogger(__name__)


class AdminDBClient(_BaseIPCClient):
    """Typed async client for DB Proxy Socket B (admin operations).

    Provides methods for admin operations: signing material,
    stats, backups, graph reads, conversations.
    """

    async def get_signing_material(self) -> dict[str, Any]:
        """Retrieve HKDF-derived key material from DB Proxy."""
        return await self.send_operation("get_signing_material")


    async def create_conversation(self, title: str = "") -> str:
        """Create one persisted conversation."""
        result = await self.send_operation(
            "create_conversation",
            {"title": title},
        )
        return str(result["conversation_id"])

    async def create_conversation_item(
        self,
        title: str = "",
    ) -> dict[str, Any] | None:
        """Create one persisted conversation and return its stored row."""
        result = await self.send_operation(
            "create_conversation",
            {"title": title},
        )
        conversation = result.get("conversation")
        if not isinstance(conversation, dict):
            return None
        return conversation

    async def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List persisted conversations ordered by most recent activity."""
        return await self._send_list_operation(
            "list_conversations",
            result_key="conversations",
            parameters={
                "limit": limit,
                "offset": offset,
            },
        )

    async def list_files(self) -> list[dict[str, Any]]:
        """List uploaded files with metadata."""
        return await self._send_list_operation(
            "list_files",
            result_key="files",
        )

    async def get_file_context(self, file_id: str) -> dict[str, Any] | None:
        """Load stored file context for one uploaded file."""
        return await self._send_optional_dict_operation(
            "get_file_context",
            result_key="file_context",
            parameters={"file_id": file_id},
        )

    async def list_chat_bridge_links(
        self,
        limit: int = 100,
        offset: int = 0,
        provider: str | None = None,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List persisted external chat bridge links."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if provider:
            params["provider"] = provider
        if conversation_id:
            params["conversation_id"] = conversation_id
        return await self._send_list_operation(
            "list_chat_bridge_links",
            result_key="chat_bridge_links",
            parameters=params,
        )

    async def create_chat_bridge_link(
        self,
        *,
        conversation_id: str,
        provider: str,
        external_space_id: str,
        external_channel_id: str,
        created_by: str,
        sync_direction: str = "bidirectional",
        delivery_mode: str = "bot_only",
        persona_mode: str = "neutral",
        persona_user_id: str = "",
    ) -> dict[str, Any]:
        """Create one persisted external chat bridge link."""
        result = await self.send_operation(
            "create_chat_bridge_link",
            {
                "conversation_id": conversation_id,
                "provider": provider,
                "external_space_id": external_space_id,
                "external_channel_id": external_channel_id,
                "created_by": created_by,
                "sync_direction": sync_direction,
                "delivery_mode": delivery_mode,
                "persona_mode": persona_mode,
                "persona_user_id": persona_user_id,
            },
        )
        return result["chat_bridge_link"]

    async def get_chat_bridge_for_conversation(
        self,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        """Load one bridge link for a conversation."""
        return await self._send_optional_dict_operation(
            "get_chat_bridge_for_conversation",
            result_key="chat_bridge_link",
            parameters={"conversation_id": conversation_id},
        )

    async def get_chat_bridge_link(
        self,
        bridge_link_id: str,
    ) -> dict[str, Any] | None:
        """Load one persisted external chat bridge link by its id."""
        return await self._send_optional_dict_operation(
            "get_chat_bridge_link",
            result_key="chat_bridge_link",
            parameters={"bridge_link_id": bridge_link_id},
        )

    async def disable_chat_bridge_link(self, bridge_link_id: str) -> bool:
        """Disable one persisted external chat bridge link."""
        result = await self.send_operation(
            "disable_chat_bridge_link",
            {"bridge_link_id": bridge_link_id},
        )
        return bool(result.get("updated", False))

    async def enable_chat_bridge_link(self, bridge_link_id: str) -> bool:
        """Enable one persisted external chat bridge link."""
        result = await self.send_operation(
            "enable_chat_bridge_link",
            {"bridge_link_id": bridge_link_id},
        )
        return bool(result.get("updated", False))

    async def update_chat_bridge_link_settings(
        self,
        *,
        bridge_link_id: str,
        delivery_mode: str,
        persona_mode: str,
        persona_user_id: str,
    ) -> dict[str, Any] | None:
        """Update one stored bridge link presentation mode."""
        result = await self.send_operation(
            "update_chat_bridge_link_settings",
            {
                "bridge_link_id": bridge_link_id,
                "delivery_mode": delivery_mode,
                "persona_mode": persona_mode,
                "persona_user_id": persona_user_id,
            },
        )
        bridge_link = result.get("chat_bridge_link")
        if not isinstance(bridge_link, dict):
            return None
        return bridge_link

    async def get_chat_bridge_message_link_by_message_id(
        self,
        *,
        bridge_link_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        """Load one bridge message mapping by internal message identifier."""
        return await self._send_optional_dict_operation(
            "get_chat_bridge_message_link_by_message_id",
            result_key="chat_bridge_message_link",
            parameters={
                "bridge_link_id": bridge_link_id,
                "message_id": message_id,
            },
        )

    async def list_chat_bridge_message_links(
        self,
        *,
        bridge_link_id: str,
        limit: int = 100,
        offset: int = 0,
        direction: str | None = None,
    ) -> list[dict[str, Any]]:
        """List stored bridge message mappings for one bridge link."""
        params: dict[str, Any] = {
            "bridge_link_id": bridge_link_id,
            "limit": limit,
            "offset": offset,
        }
        if direction:
            params["direction"] = direction
        return await self._send_list_operation(
            "list_chat_bridge_message_links",
            result_key="chat_bridge_message_links",
            parameters=params,
        )

    async def delete_conversation(
        self,
        conversation_id: str,
    ) -> None:
        """Delete a conversation and cascade related data."""
        await self.send_operation(
            "delete_conversation",
            {
                "conversation_id": conversation_id,
            },
        )

    async def update_conversation_title(
        self,
        conversation_id: str,
        title: str,
    ) -> dict[str, Any] | None:
        """Update one conversation title and return the stored conversation."""
        return await self._send_optional_dict_operation(
            "update_conversation_title",
            result_key="conversation",
            parameters={
                "conversation_id": conversation_id,
                "title": title,
            },
        )

    async def update_conversation_processing_state(
        self,
        conversation_id: str,
        processing_status: str,
        processing_stage: str = "",
        processing_started_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Update one conversation processing state and return the stored row."""
        return await self._send_optional_dict_operation(
            "update_conversation_processing_state",
            result_key="conversation",
            parameters={
                "conversation_id": conversation_id,
                "processing_status": processing_status,
                "processing_stage": processing_stage,
                "processing_started_at": processing_started_at,
            },
        )

    async def get_stats(self) -> dict[str, int]:
        """Get database statistics."""
        return await self.send_operation("get_stats")

    async def get_user_profile(self) -> dict:
        """Get user profile from DB Proxy."""
        return await self.send_operation("get_user_profile")

    async def store_user_profile(self, profile: dict) -> dict:
        """Store user profile via DB Proxy."""
        return await self.send_operation("store_user_profile", profile)

    async def store_avatar(self, avatar_type: str, data: str, mime_type: str) -> dict:
        """Store avatar image via DB Proxy.

        Args:
            avatar_type: Avatar type ("user" or "ai").
            data: Base64-encoded image data.
            mime_type: Image MIME type.

        Returns:
            Success dict.
        """
        return await self.send_operation(
            "store_avatar",
            {"avatar_type": avatar_type, "data": data, "mime_type": mime_type},
        )

    async def get_avatar(self, avatar_type: str) -> dict:
        """Get avatar image from DB Proxy.

        Args:
            avatar_type: Avatar type ("user" or "ai").

        Returns:
            Dict with avatar data or None.
        """
        return await self.send_operation("get_avatar", {"avatar_type": avatar_type})

    async def delete_avatar(self, avatar_type: str) -> dict:
        """Delete avatar via DB Proxy.

        Args:
            avatar_type: Avatar type ("user" or "ai").

        Returns:
            Success dict.
        """
        return await self.send_operation("delete_avatar", {"avatar_type": avatar_type})

    # --- Scheduled tasks ---

    async def create_scheduled_task(self, params: dict) -> dict:
        """Create a new scheduled task."""
        return await self.send_operation("create_scheduled_task", params)

    async def list_scheduled_tasks(self, status: str = "all") -> dict:
        """List scheduled tasks with optional status filter."""
        return await self.send_operation("list_scheduled_tasks", {"status": status})

    async def update_scheduled_task(self, params: dict) -> dict:
        """Update a scheduled task."""
        return await self.send_operation("update_scheduled_task", params)

    async def delete_scheduled_task(self, task_id: str) -> dict:
        """Delete a scheduled task."""
        return await self.send_operation("delete_scheduled_task", {"task_id": task_id})

    async def list_task_runs(self, task_id: str, limit: int = 20) -> dict:
        """List recent runs for a scheduled task."""
        return await self.send_operation(
            "list_task_runs",
            {"task_id": task_id, "limit": limit},
        )

    async def list_records(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List records with full metadata."""
        return await self._send_list_operation(
            "list_records",
            result_key="records",
            parameters={
                "limit": limit,
                "offset": offset,
            },
        )


    async def list_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List messages with metadata."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if conversation_id:
            params["conversation_id"] = conversation_id
        return await self._send_list_operation(
            "list_messages",
            result_key="messages",
            parameters=params,
        )

    async def list_pending_actions(
        self,
        status: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List confirmation-required actions, optionally filtered by status."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if run_id:
            params["run_id"] = run_id
        return await self._send_list_operation(
            "list_pending_actions",
            result_key="pending_actions",
            parameters=params or None,
        )

    async def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        """Return one agent run row (which carries its conversation_id)."""
        return await self._send_optional_dict_operation(
            "get_agent_run",
            result_key="agent_run",
            parameters={"run_id": run_id},
        )

    async def list_agent_run_events(
        self,
        run_id: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return the recorded event stream for one agent run, in order."""
        return await self._send_list_operation(
            "list_agent_run_events",
            result_key="events",
            parameters={"run_id": run_id, "limit": limit},
        )

    async def list_agent_runs(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return recent agent runs, newest first."""
        return await self._send_list_operation(
            "list_agent_runs",
            result_key="runs",
            parameters={"limit": limit, "offset": offset},
        )

    async def count_memory_entries(self) -> dict[str, int]:
        """Count messages, records, and assertions."""
        return await self.send_operation("count_memory_entries")

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Load one graph entity by id."""
        result = await self.send_operation(
            "get_entity",
            {"entity_id": entity_id},
        )
        return result.get("entity")

    async def find_entities(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Find graph entities by full-text query."""
        result = await self.send_operation(
            "find_entities",
            {"query": query, "limit": limit},
        )
        return result["entities"]

    async def get_entities_by_ids(
        self,
        entity_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Load several graph entities by id (neighbor resolution)."""
        result = await self.send_operation(
            "get_entities_by_ids",
            {"entity_ids": entity_ids},
        )
        return result["entities"]

    async def list_entities(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List graph entities newest-first with active-assertion counts."""
        return await self.send_operation(
            "list_entities",
            {"limit": limit, "offset": offset},
        )

    async def peek_record_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read pending record-queue rows for diagnostics (no pruning)."""
        result = await self.send_operation(
            "peek_record_queue",
            {"limit": limit},
        )
        return result["items"]

    async def get_vector_coverage(self) -> dict[str, Any]:
        """Return embedding coverage over non-tainted records."""
        return await self.send_operation("get_vector_coverage", {})

    async def list_assertions_for_entity(
        self,
        entity_id: str,
        valid_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List assertions where the entity is subject or object."""
        result = await self.send_operation(
            "list_assertions_for_entity",
            {"entity_id": entity_id, "valid_only": valid_only, "limit": limit},
        )
        return result["assertions"]

    async def find_graph_entry_points(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Find labeled graph entry points for a query."""
        result = await self.send_operation(
            "find_graph_entry_points",
            {"query": query, "limit": limit},
        )
        return result["entry_points"]

    async def get_ui_settings(self) -> dict[str, Any]:
        """Read the web-tier UI settings blob."""
        result = await self.send_operation("get_ui_settings")
        return result.get("settings", {})

    async def store_ui_settings(self, settings: dict[str, Any]) -> None:
        """Replace the web-tier UI settings blob."""
        await self.send_operation(
            "store_ui_settings",
            {"settings": settings},
        )

    async def get_graph_stats(self) -> dict[str, Any]:
        """Return the graph statistics snapshot."""
        return await self.send_operation("get_graph_stats")

    async def create_backup(self) -> dict[str, Any]:
        """Write consistent encrypted copies of every open database.

        Returns:
            Dict with "backup_paths" mapping database name to file path.
        """
        return await self.send_operation("create_backup")

    async def get_loop_heartbeats(self) -> dict[str, str]:
        """Return last-success timestamps of the background loops."""
        result = await self.send_operation("get_loop_heartbeats")
        return result.get("heartbeats", {})

    async def get_memory_context_view(self) -> dict[str, Any]:
        """Return the memory context snapshot through DB proxy."""
        return await self.send_operation("get_memory_context_view")

    async def get_memory_quality_report(self) -> dict[str, Any]:
        """Return the memory quality report through DB proxy."""
        return await self.send_operation("get_memory_quality_report")

    async def get_memory_timeline(
        self,
        entry_type: str = "",
        sort: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return the memory timeline through DB proxy."""
        return await self.send_operation(
            "get_memory_timeline",
            {
                "type": entry_type,
                "sort": sort,
                "limit": limit,
                "offset": offset,
            },
        )

    async def list_audit_log(
        self,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List audit log entries."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if event_type:
            params["event_type"] = event_type
        return await self._send_list_operation(
            "list_audit_log",
            result_key="entries",
            parameters=params,
        )

    async def list_memory_route_diagnostics(
        self,
        limit: int = 50,
        offset: int = 0,
        final_intent: str | None = None,
        mismatch_only: bool = False,
        fallback_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List structured memory-route diagnostics for admin debugging."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "mismatch_only": mismatch_only,
            "fallback_only": fallback_only,
        }
        if final_intent:
            params["final_intent"] = final_intent
        return await self._send_list_operation(
            "list_memory_route_diagnostics",
            result_key="diagnostics",
            parameters=params,
        )

    async def get_memory_route_diagnostics_summary(
        self,
        recent_hours: int | None = None,
    ) -> dict[str, Any]:
        """Return aggregated route-diagnostics counts for admin debugging."""
        params: dict[str, Any] | None = None
        if recent_hours is not None:
            params = {"recent_hours": recent_hours}
        return await self.send_operation(
            "get_memory_route_diagnostics_summary",
            params,
        )

    async def list_record_processing_diagnostics(
        self,
        limit: int = 100,
        decision: str | None = None,
    ) -> list[dict[str, Any]]:
        """List record-processing diagnostics from diagnostics DB."""
        params: dict[str, Any] = {"limit": limit}
        if decision:
            params["decision"] = decision
        return await self._send_list_operation(
            "list_record_processing_diagnostics",
            result_key="entries",
            parameters=params,
        )

    async def list_summary_processing_diagnostics(
        self,
        limit: int = 100,
        decision: str | None = None,
    ) -> list[dict[str, Any]]:
        """List summary-processing diagnostics from diagnostics DB."""
        params: dict[str, Any] = {"limit": limit}
        if decision:
            params["decision"] = decision
        return await self._send_list_operation(
            "list_summary_processing_diagnostics",
            result_key="entries",
            parameters=params,
        )

    async def write_audit(
        self,
        event_type: str,
        tool_name: str | None = None,
        parameters: str | None = None,
        result: str | None = None,
        zone: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Write an audit log entry via DB Proxy."""
        try:
            await self.send_operation(
                "write_audit",
                {
                    "event_type": event_type,
                    "tool_name": tool_name,
                    "parameters": parameters,
                    "result": result,
                    "zone": zone,
                    "duration_ms": duration_ms,
                },
            )
        except Exception:
            logger.warning("Audit write failed (event_type=%s)", event_type)

    async def list_skills(self) -> list[dict[str, Any]]:
        """List all skills as a compact index."""
        result = await self.send_operation("skill_list", {})
        return result.get("skills", [])

    async def delete_skill(self, name: str) -> dict[str, Any]:
        """Delete a skill by name."""
        return await self.send_operation("skill_delete", {"name": name})

    async def update_skill(self, name: str, **fields: object) -> dict[str, Any]:
        """Update skill fields by name."""
        params: dict[str, Any] = {"name": name}
        for key in (
            "title",
            "description",
            "trigger",
            "procedure",
            "errors",
            "verification",
            "enabled",
        ):
            if key in fields and fields[key] is not None:
                params[key] = fields[key]
        return await self.send_operation("skill_update", params)

    async def get_skill(self, name: str) -> dict[str, Any] | None:
        """Load a single skill by name with full data."""
        result = await self.send_operation("skill_load", {"name": name})
        return result.get("skill")

    async def list_skill_usage_logs(self, skill_id: str) -> list[dict[str, Any]]:
        """List usage logs for a skill."""
        result = await self.send_operation(
            "skill_usage_log_list", {"skill_id": skill_id}
        )
        return result.get("logs", [])

    async def list_chat_settings(
        self,
        conversation_id: str,
    ) -> dict[str, str]:
        """Return all chat settings for one conversation as a flat dict."""
        result = await self.send_operation(
            "list_chat_settings",
            {"conversation_id": conversation_id},
        )
        return result.get("settings", {})

    async def list_all_chat_settings(self) -> dict[str, dict[str, str]]:
        """Return chat settings grouped by conversation_id."""
        result = await self.send_operation(
            "list_chat_settings",
            {},
        )
        return result.get("all_settings", {})

    async def upsert_chat_settings(
        self,
        conversation_id: str,
        entries: list[dict[str, str]],
    ) -> dict[str, str]:
        """Insert or replace chat settings for one conversation.

        Args:
            conversation_id: Target conversation.
            entries: List of {"key": ..., "value": ...} dicts.

        Returns:
            Full settings dict after upsert.
        """
        result = await self.send_operation(
            "upsert_chat_settings",
            {
                "conversation_id": conversation_id,
                "entries": entries,
            },
        )
        return result.get("settings", {})

    # --- MCP read operation (Socket B; memory writes go via the orchestrator) ---

    async def fulltext_search(
        self,
        query: str,
        table: str = "messages",
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search messages or records via FTS5.

        Args:
            query: Full-text search query string.
            table: Table to search ("messages" or "records").
            limit: Maximum results to return.
            offset: Pagination offset.

        Returns:
            List of result dicts with "id", "rowid", "created_at", and the
            source text of the hit ("content" plus "role" for messages,
            "summary" for records).
        """
        result = await self.send_operation(
            "fulltext_search",
            {
                "query": query,
                "table": table,
                "limit": limit,
                "offset": offset,
            },
        )
        return result.get("results", [])

    async def mcp_memory_recent(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return the most recently created memory entries, newest-first.

        Reads across messages/records/assertions via Socket B.
        Bounded by the handler's MAX_RECENT_WINDOW.

        Args:
            limit: Maximum entries to return.
            offset: Pagination offset.

        Returns:
            List of entry dicts, newest-first, each tagged with "table".
        """
        return await self._send_list_operation(
            "mcp_memory_recent",
            result_key="entries",
            parameters={"limit": limit, "offset": offset},
        )

    async def mcp_memory_get(
        self,
        entry_id: str,
        table: str = "messages",
    ) -> dict[str, Any] | None:
        """Fetch a single memory entry by (table, id) via Socket B.

        Args:
            entry_id: The entry's id.
            table: One of "messages", "records", "assertions".

        Returns:
            Entry dict or None if not found.
        """
        return await self._send_optional_dict_operation(
            "mcp_memory_get",
            result_key="entry",
            parameters={"entry_id": entry_id, "table": table},
        )

    async def mcp_memory_stats(self) -> dict[str, Any]:
        """Get memory-specific statistics: counts + date range.

        Returns totals, per-type counts, and created_at range
        without any entry content.
        """
        return await self.send_operation("mcp_memory_stats")
