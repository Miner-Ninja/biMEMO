"""Channel constants, operation sets, and registry/allowlist builders."""

from typing import TYPE_CHECKING

from backend.ipc.db_proxy import (
    ChannelAllowlist,
    OperationRegistry,
    create_health_handler,
)

from .handlers.admin import (
    make_count_memory_entries_handler,
    make_create_backup_handler,
    make_delete_avatar_handler,
    make_get_avatar_handler,
    make_get_password_hash_handler,
    make_get_recent_rate_limit_calls_handler,
    make_get_signing_material_handler,
    make_get_stats_handler,
    make_get_ui_settings_handler,
    make_get_user_profile_handler,
    make_initialize_handler,
    make_list_audit_log_handler,
    make_list_messages_handler,
    make_list_records_handler,
    make_mcp_memory_get_handler,
    make_mcp_memory_recent_handler,
    make_mcp_memory_stats_handler,
    make_record_rate_limit_call_handler,
    make_store_avatar_handler,
    make_store_password_hash_handler,
    make_store_ui_settings_handler,
    make_store_user_profile_handler,
)
from .handlers.chat_bridge import (
    make_create_chat_bridge_link_handler,
    make_create_chat_bridge_message_link_handler,
    make_disable_chat_bridge_link_handler,
    make_enable_chat_bridge_link_handler,
    make_get_chat_bridge_by_external_target_handler,
    make_get_chat_bridge_for_conversation_handler,
    make_get_chat_bridge_link_handler,
    make_get_chat_bridge_message_link_by_external_message_handler,
    make_get_chat_bridge_message_link_by_message_id_handler,
    make_list_chat_bridge_links_handler,
    make_list_chat_bridge_message_links_handler,
    make_update_chat_bridge_link_settings_handler,
)
from .handlers.chat_settings import (
    make_list_chat_settings_handler,
    make_upsert_chat_settings_handler,
)
from .handlers.conversation import (
    make_create_conversation_handler,
    make_delete_conversation_handler,
    make_list_conversations_handler,
    make_reset_stuck_processing_states_handler,
    make_update_conversation_processing_state_handler,
    make_update_conversation_title_handler,
)
from .handlers.core import (
    make_bump_queued_record_attempts_handler,
    make_delete_queued_records_handler,
    make_fetch_unembedded_records_handler,
    make_fulltext_search_handler,
    make_get_vector_coverage_handler,
    make_init_vector_table_handler,
    make_list_queued_records_handler,
    make_load_context_handler,
    make_load_recent_messages_handler,
    make_peek_record_queue_handler,
    make_prune_unlinked_records_handler,
    make_save_conversation_summary_handler,
    make_save_message_handler,
    make_save_queued_record_handler,
    make_save_record_handler,
    make_store_embedding_handler,
    make_vector_search_handler,
    make_verify_chain_handler,
    make_write_audit_handler,
)
from .handlers.diagnostic import (
    make_get_memory_route_diagnostics_summary_handler,
    make_list_memory_route_diagnostics_handler,
    make_list_record_processing_diagnostics_handler,
    make_list_summary_processing_diagnostics_handler,
    make_write_memory_route_diagnostic_handler,
    make_write_record_processing_diagnostic_handler,
    make_write_summary_processing_diagnostic_handler,
)
from .handlers.file import (
    make_create_file_metadata_handler,
    make_delete_file_metadata_handler,
    make_get_file_context_handler,
    make_get_file_metadata_handler,
    make_list_files_handler,
    make_save_file_context_handler,
    make_save_file_context_usage_handler,
)
from .handlers.graph import (
    make_fetch_unextracted_records_handler,
    make_find_entities_handler,
    make_find_graph_entry_points_handler,
    make_get_entities_by_ids_handler,
    make_get_entity_handler,
    make_get_graph_stats_handler,
    make_invalidate_assertion_handler,
    make_list_assertions_for_entity_handler,
    make_list_entities_handler,
    make_prune_graph_handler,
    make_save_assertion_handler,
    make_save_entity_handler,
)
from .handlers.integration import (
    make_activate_llm_config_handler,
    make_create_integration_handler,
    make_create_llm_config_handler,
    make_delete_credential_handler,
    make_delete_integration_handler,
    make_delete_llm_config_handler,
    make_get_credential_handler,
    make_get_integration_config_handler,
    make_get_llm_config_full_handler,
    make_get_llm_config_meta_handler,
    make_list_integrations_handler,
    make_list_llm_configs_handler,
    make_store_credential_handler,
    make_store_llm_config_handler,
    make_update_llm_config_handler,
)
from .handlers.memory_debug import (
    make_get_memory_context_view_handler,
    make_get_memory_quality_report_handler,
    make_get_memory_timeline_handler,
)
from .handlers.permissions import (
    make_add_project_root_handler,
    make_complete_pending_action_handler,
    make_create_agent_run_event_handler,
    make_create_agent_run_handler,
    make_create_capability_grant_handler,
    make_create_pending_action_handler,
    make_decide_pending_action_handler,
    make_expire_capability_grants_handler,
    make_expire_pending_actions_handler,
    make_get_agent_run_handler,
    make_get_pending_action_handler,
    make_get_resume_snapshot_handler,
    make_list_agent_run_events_handler,
    make_list_capability_grants_handler,
    make_list_pending_actions_handler,
    make_list_project_roots_handler,
    make_lookup_capability_grant_handler,
    make_prune_runtime_state_handler,
    make_remove_project_root_handler,
    make_rename_project_root_handler,
    make_resolve_project_root_handler,
    make_revoke_capability_grant_handler,
    make_update_agent_run_status_handler,
)
from .handlers.scheduler import (
    make_advance_task_next_run_handler,
    make_create_scheduled_task_handler,
    make_delete_scheduled_task_handler,
    make_get_due_tasks_handler,
    make_list_scheduled_tasks_handler,
    make_list_task_runs_handler,
    make_mark_task_run_handler,
    make_record_task_run_handler,
    make_update_scheduled_task_handler,
)
from .handlers.skill import (
    make_skill_create_handler,
    make_skill_delete_handler,
    make_skill_list_handler,
    make_skill_load_handler,
    make_skill_search_handler,
    make_skill_update_handler,
    make_skill_usage_log_create_handler,
    make_skill_usage_log_list_handler,
    make_skill_use_handler,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


# --- Channel identifiers ---
CHANNEL_A = "socket_a"
CHANNEL_B = "socket_b"
CHANNEL_C = "socket_c"

# ============================================================
# Socket operation sets
# ============================================================

SOCKET_A_OPERATIONS = {
    "save_message",
    "save_conversation_summary",
    "load_recent_messages",
    "list_messages",
    "fulltext_search",
    "vector_search",
    "load_context",
    "write_audit",
    "verify_chain",
    "save_record",
    "save_queued_record",
    "save_entity",
    "save_assertion",
    "invalidate_assertion",
    "get_entity",
    "find_entities",
    "list_assertions_for_entity",
    "find_graph_entry_points",
    "fetch_unextracted_records",
    "prune_graph",
    "get_graph_stats",
    "delete_queued_records",
    "list_queued_records",
    "bump_queued_record_attempts",
    "store_embedding",
    "prune_unlinked_records",
    "create_conversation",
    "list_conversations",
    "delete_conversation",
    "update_conversation_title",
    "update_conversation_processing_state",
    "reset_stuck_processing_states",
    "get_integration_config",
    "record_rate_limit_call",
    "get_recent_rate_limit_calls",
    "init_vector_table",
    "fetch_unembedded_records",
    "store_llm_config",
    "get_user_profile",
    "get_avatar",
    "create_scheduled_task",
    "list_scheduled_tasks",
    "get_due_tasks",
    "advance_task_next_run",
    "mark_task_run",
    "record_task_run",
    "update_scheduled_task",
    "delete_scheduled_task",
    "create_file_metadata",
    "list_files",
    "delete_file_metadata",
    "get_file_metadata",
    "save_file_context",
    "save_file_context_usage",
    "get_file_context",
    "create_chat_bridge_link",
    "list_chat_bridge_links",
    "get_chat_bridge_for_conversation",
    "get_chat_bridge_link",
    "get_chat_bridge_by_external_target",
    "disable_chat_bridge_link",
    "enable_chat_bridge_link",
    "update_chat_bridge_link_settings",
    "create_chat_bridge_message_link",
    "list_chat_bridge_message_links",
    "get_chat_bridge_message_link_by_external_message",
    "get_chat_bridge_message_link_by_message_id",
    "write_memory_route_diagnostic",
    "write_record_processing_diagnostic",
    "write_summary_processing_diagnostic",
    "skill_create",
    "skill_search",
    "skill_use",
    "skill_list",
    "skill_delete",
    "skill_load",
    "skill_update",
    "skill_usage_log_create",
    "skill_usage_log_list",
    "list_chat_settings",
    "upsert_chat_settings",
    "list_project_roots",
    "add_project_root",
    "remove_project_root",
    "rename_project_root",
    "resolve_project_root",
    "list_capability_grants",
    "create_capability_grant",
    "lookup_capability_grant",
    "revoke_capability_grant",
    "expire_capability_grants",
    "create_agent_run",
    "get_agent_run",
    "create_agent_run_event",
    "list_agent_run_events",
    "update_agent_run_status",
    "create_pending_action",
    "get_pending_action",
    "get_resume_snapshot",
    "list_pending_actions",
    "decide_pending_action",
    "complete_pending_action",
    "expire_pending_actions",
    "prune_runtime_state",
}

# Socket B adds admin, auth, schema (no credentials — those go via Socket C)
SOCKET_B_ONLY_OPERATIONS = {
    "create_conversation",
    "create_backup",
    "get_stats",
    "get_signing_material",
    "store_password_hash",
    "get_password_hash",
    "initialize",
    "get_user_profile",
    "store_user_profile",
    "get_ui_settings",
    "store_ui_settings",
    "list_entities",
    "get_entities_by_ids",
    "peek_record_queue",
    "get_vector_coverage",
    "get_avatar",
    "store_avatar",
    "delete_avatar",
    "get_due_tasks",
    "advance_task_next_run",
    "mark_task_run",
    "record_task_run",
    "list_task_runs",
    "list_records",
    "list_messages",
    "count_memory_entries",
    "list_audit_log",
    "list_conversations",
    "list_memory_route_diagnostics",
    "get_memory_route_diagnostics_summary",
    "list_record_processing_diagnostics",
    "list_summary_processing_diagnostics",
    "get_memory_context_view",
    "get_memory_quality_report",
    "get_memory_timeline",
    "delete_conversation",
    "update_conversation_title",
    "update_conversation_processing_state",
    "skill_list",
    "skill_load",
    "skill_delete",
    "skill_update",
    "skill_usage_log_list",
    "list_chat_settings",
    "upsert_chat_settings",
    "list_project_roots",
    "add_project_root",
    "remove_project_root",
    "rename_project_root",
    "resolve_project_root",
    "list_capability_grants",
    "create_capability_grant",
    "lookup_capability_grant",
    "revoke_capability_grant",
    "expire_capability_grants",
    "create_agent_run",
    "get_agent_run",
    "create_agent_run_event",
    "list_agent_run_events",
    "update_agent_run_status",
    "create_pending_action",
    "get_pending_action",
    "get_resume_snapshot",
    "list_pending_actions",
    "decide_pending_action",
    "complete_pending_action",
    "expire_pending_actions",
    "prune_runtime_state",
}

SOCKET_B_OPERATIONS = SOCKET_B_ONLY_OPERATIONS | {
    "get_graph_stats",
    "get_entity",
    "find_entities",
    "list_assertions_for_entity",
    "find_graph_entry_points",
    "fulltext_search",   # MCP search_memory tool (read-only; writes go via Socket A)
    "mcp_memory_recent",  # MCP recent_memory (read-only)
    "mcp_memory_get",   # MCP get_memory (read-only)
    "mcp_memory_stats",   # MCP memory_stats (read-only)
    "write_audit",
    "create_scheduled_task",
    "list_scheduled_tasks",
    "update_scheduled_task",
    "delete_scheduled_task",
    "create_file_metadata",
    "list_files",
    "delete_file_metadata",
    "get_file_metadata",
    "save_file_context",
    "save_file_context_usage",
    "get_file_context",
    "create_chat_bridge_link",
    "list_chat_bridge_links",
    "get_chat_bridge_for_conversation",
    "get_chat_bridge_link",
    "get_chat_bridge_by_external_target",
    "disable_chat_bridge_link",
    "enable_chat_bridge_link",
    "update_chat_bridge_link_settings",
    "create_chat_bridge_message_link",
    "list_chat_bridge_message_links",
    "get_chat_bridge_message_link_by_external_message",
    "get_chat_bridge_message_link_by_message_id",
}

# Socket C: credential and integration operations (Credential Proxy access)
SOCKET_C_OPERATIONS = {
    "store_credential",
    "get_credential",
    "delete_credential",
    "create_integration",
    "list_integrations",
    "delete_integration",
    "store_llm_config",
    "get_llm_config_meta",
    "list_llm_configs",
    "create_llm_config",
    "update_llm_config",
    "delete_llm_config",
    "activate_llm_config",
    "get_llm_config_full",
}


def _register_message_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register core message and context operations."""
    registry.register("save_message", make_save_message_handler(db))
    registry.register("load_recent_messages", make_load_recent_messages_handler(db))
    registry.register("fulltext_search", make_fulltext_search_handler(db))
    registry.register("vector_search", make_vector_search_handler(db))
    registry.register("load_context", make_load_context_handler(db))
    registry.register(
        "save_conversation_summary",
        make_save_conversation_summary_handler(db),
    )


def _register_diagnostic_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
    diagnostics_db: DatabaseManager | None,
    diagnostics_retention_days: int,
) -> None:
    """Register audit and optional diagnostics operations."""
    registry.register("write_audit", make_write_audit_handler(db))
    registry.register("verify_chain", make_verify_chain_handler(db))
    if diagnostics_db is None:
        return
    registry.register(
        "write_memory_route_diagnostic",
        make_write_memory_route_diagnostic_handler(
            diagnostics_db,
            retention_days=diagnostics_retention_days,
        ),
    )
    registry.register(
        "write_record_processing_diagnostic",
        make_write_record_processing_diagnostic_handler(diagnostics_db),
    )
    registry.register(
        "write_summary_processing_diagnostic",
        make_write_summary_processing_diagnostic_handler(diagnostics_db),
    )
    registry.register(
        "list_memory_route_diagnostics",
        make_list_memory_route_diagnostics_handler(diagnostics_db),
    )
    registry.register(
        "get_memory_route_diagnostics_summary",
        make_get_memory_route_diagnostics_summary_handler(diagnostics_db),
    )
    registry.register(
        "list_record_processing_diagnostics",
        make_list_record_processing_diagnostics_handler(diagnostics_db),
    )
    registry.register(
        "list_summary_processing_diagnostics",
        make_list_summary_processing_diagnostics_handler(diagnostics_db),
    )


def _register_memory_debug_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register admin memory-debug read operations."""
    registry.register(
        "get_memory_context_view",
        make_get_memory_context_view_handler(db),
    )
    registry.register(
        "get_memory_quality_report",
        make_get_memory_quality_report_handler(db),
    )
    registry.register(
        "get_memory_timeline",
        make_get_memory_timeline_handler(db),
    )


def _register_graph_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register graph entity/assertion storage operations."""
    registry.register("save_entity", make_save_entity_handler(db))
    registry.register("save_assertion", make_save_assertion_handler(db))
    registry.register(
        "invalidate_assertion",
        make_invalidate_assertion_handler(db),
    )
    registry.register(
        "fetch_unextracted_records",
        make_fetch_unextracted_records_handler(db),
    )
    registry.register("prune_graph", make_prune_graph_handler(db))
    registry.register("get_graph_stats", make_get_graph_stats_handler(db))
    registry.register("get_entity", make_get_entity_handler(db))
    registry.register(
        "get_entities_by_ids",
        make_get_entities_by_ids_handler(db),
    )
    registry.register("find_entities", make_find_entities_handler(db))
    registry.register("list_entities", make_list_entities_handler(db))
    registry.register(
        "list_assertions_for_entity",
        make_list_assertions_for_entity_handler(db),
    )
    registry.register(
        "find_graph_entry_points",
        make_find_graph_entry_points_handler(db),
    )


def _register_memory_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register records, summaries, and reminders operations."""
    registry.register("save_record", make_save_record_handler(db))
    registry.register(
        "save_queued_record",
        make_save_queued_record_handler(db),
    )
    registry.register(
        "delete_queued_records",
        make_delete_queued_records_handler(db),
    )
    registry.register(
        "list_queued_records",
        make_list_queued_records_handler(db),
    )
    registry.register(
        "peek_record_queue",
        make_peek_record_queue_handler(db),
    )
    registry.register(
        "get_vector_coverage",
        make_get_vector_coverage_handler(db),
    )
    registry.register(
        "bump_queued_record_attempts",
        make_bump_queued_record_attempts_handler(db),
    )
    registry.register("store_embedding", make_store_embedding_handler(db))
    registry.register(
        "prune_unlinked_records",
        make_prune_unlinked_records_handler(db),
    )
    registry.register("skill_create", make_skill_create_handler(db))
    registry.register("skill_search", make_skill_search_handler(db))
    registry.register("skill_use", make_skill_use_handler(db))
    registry.register("skill_list", make_skill_list_handler(db))
    registry.register("skill_delete", make_skill_delete_handler(db))
    registry.register("skill_load", make_skill_load_handler(db))
    registry.register("skill_update", make_skill_update_handler(db))
    registry.register("skill_usage_log_create", make_skill_usage_log_create_handler(db))
    registry.register("skill_usage_log_list", make_skill_usage_log_list_handler(db))


def _register_conversation_and_bridge_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register conversation and chat-bridge operations."""
    registry.register("create_conversation", make_create_conversation_handler(db))
    registry.register("list_conversations", make_list_conversations_handler(db))
    registry.register("delete_conversation", make_delete_conversation_handler(db))
    registry.register(
        "update_conversation_title",
        make_update_conversation_title_handler(db),
    )
    registry.register(
        "update_conversation_processing_state",
        make_update_conversation_processing_state_handler(db),
    )
    registry.register(
        "reset_stuck_processing_states",
        make_reset_stuck_processing_states_handler(db),
    )
    registry.register(
        "create_chat_bridge_link",
        make_create_chat_bridge_link_handler(db),
    )
    registry.register(
        "list_chat_bridge_links",
        make_list_chat_bridge_links_handler(db),
    )
    registry.register(
        "get_chat_bridge_for_conversation",
        make_get_chat_bridge_for_conversation_handler(db),
    )
    registry.register(
        "get_chat_bridge_link",
        make_get_chat_bridge_link_handler(db),
    )
    registry.register(
        "get_chat_bridge_by_external_target",
        make_get_chat_bridge_by_external_target_handler(db),
    )
    registry.register(
        "disable_chat_bridge_link",
        make_disable_chat_bridge_link_handler(db),
    )
    registry.register(
        "enable_chat_bridge_link",
        make_enable_chat_bridge_link_handler(db),
    )
    registry.register(
        "update_chat_bridge_link_settings",
        make_update_chat_bridge_link_settings_handler(db),
    )
    registry.register(
        "create_chat_bridge_message_link",
        make_create_chat_bridge_message_link_handler(db),
    )
    registry.register(
        "list_chat_bridge_message_links",
        make_list_chat_bridge_message_links_handler(db),
    )
    registry.register(
        "get_chat_bridge_message_link_by_external_message",
        make_get_chat_bridge_message_link_by_external_message_handler(db),
    )
    registry.register(
        "get_chat_bridge_message_link_by_message_id",
        make_get_chat_bridge_message_link_by_message_id_handler(db),
    )
    registry.register(
        "list_chat_settings",
        make_list_chat_settings_handler(db),
    )
    registry.register(
        "upsert_chat_settings",
        make_upsert_chat_settings_handler(db),
    )


def _register_permission_operations(
    registry: OperationRegistry,
    core_db: DatabaseManager,
    runtime_db: DatabaseManager,
) -> None:
    """Register project root, grant, and action lifecycle operations."""
    registry.register("list_project_roots", make_list_project_roots_handler(core_db))
    registry.register("add_project_root", make_add_project_root_handler(core_db))
    registry.register("remove_project_root", make_remove_project_root_handler(core_db))
    registry.register("rename_project_root", make_rename_project_root_handler(core_db))
    registry.register(
        "resolve_project_root",
        make_resolve_project_root_handler(core_db),
    )
    registry.register(
        "list_capability_grants",
        make_list_capability_grants_handler(core_db),
    )
    registry.register(
        "create_capability_grant",
        make_create_capability_grant_handler(core_db),
    )
    registry.register(
        "lookup_capability_grant",
        make_lookup_capability_grant_handler(core_db),
    )
    registry.register(
        "revoke_capability_grant",
        make_revoke_capability_grant_handler(core_db),
    )
    registry.register(
        "expire_capability_grants",
        make_expire_capability_grants_handler(core_db),
    )
    registry.register("create_agent_run", make_create_agent_run_handler(runtime_db))
    registry.register("get_agent_run", make_get_agent_run_handler(runtime_db))
    registry.register(
        "create_agent_run_event",
        make_create_agent_run_event_handler(runtime_db),
    )
    registry.register(
        "list_agent_run_events",
        make_list_agent_run_events_handler(runtime_db),
    )
    registry.register(
        "update_agent_run_status",
        make_update_agent_run_status_handler(runtime_db),
    )
    registry.register(
        "create_pending_action",
        make_create_pending_action_handler(runtime_db),
    )
    registry.register(
        "get_pending_action",
        make_get_pending_action_handler(runtime_db),
    )
    registry.register(
        "get_resume_snapshot",
        make_get_resume_snapshot_handler(runtime_db),
    )
    registry.register(
        "list_pending_actions",
        make_list_pending_actions_handler(runtime_db),
    )
    registry.register(
        "decide_pending_action",
        make_decide_pending_action_handler(runtime_db),
    )
    registry.register(
        "complete_pending_action",
        make_complete_pending_action_handler(runtime_db),
    )
    registry.register(
        "expire_pending_actions",
        make_expire_pending_actions_handler(runtime_db),
    )
    registry.register(
        "prune_runtime_state",
        make_prune_runtime_state_handler(runtime_db),
    )


def _register_integration_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register credentials, integrations, and LLM config operations."""
    registry.register("store_credential", make_store_credential_handler(db))
    registry.register("get_credential", make_get_credential_handler(db))
    registry.register("delete_credential", make_delete_credential_handler(db))
    registry.register("create_integration", make_create_integration_handler(db))
    registry.register("list_integrations", make_list_integrations_handler(db))
    registry.register("delete_integration", make_delete_integration_handler(db))
    registry.register("get_integration_config", make_get_integration_config_handler(db))
    registry.register("store_llm_config", make_store_llm_config_handler(db))
    registry.register("get_llm_config_meta", make_get_llm_config_meta_handler(db))
    registry.register("list_llm_configs", make_list_llm_configs_handler(db))
    registry.register("create_llm_config", make_create_llm_config_handler(db))
    registry.register("update_llm_config", make_update_llm_config_handler(db))
    registry.register("delete_llm_config", make_delete_llm_config_handler(db))
    registry.register("activate_llm_config", make_activate_llm_config_handler(db))
    registry.register("get_llm_config_full", make_get_llm_config_full_handler(db))


def _register_misc_db_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register rate-limit, vector-table, user, file, and admin operations."""
    registry.register(
        "record_rate_limit_call",
        make_record_rate_limit_call_handler(db),
    )
    registry.register(
        "get_recent_rate_limit_calls",
        make_get_recent_rate_limit_calls_handler(db),
    )
    registry.register("init_vector_table", make_init_vector_table_handler(db))
    registry.register(
        "fetch_unembedded_records",
        make_fetch_unembedded_records_handler(db),
    )
    registry.register("get_user_profile", make_get_user_profile_handler(db))
    registry.register("store_user_profile", make_store_user_profile_handler(db))
    registry.register("get_ui_settings", make_get_ui_settings_handler(db))
    registry.register(
        "store_ui_settings",
        make_store_ui_settings_handler(db),
    )
    registry.register("get_avatar", make_get_avatar_handler(db))
    registry.register("store_avatar", make_store_avatar_handler(db))
    registry.register("delete_avatar", make_delete_avatar_handler(db))
    registry.register("create_scheduled_task", make_create_scheduled_task_handler(db))
    registry.register("list_scheduled_tasks", make_list_scheduled_tasks_handler(db))
    registry.register("get_due_tasks", make_get_due_tasks_handler(db))
    registry.register("advance_task_next_run", make_advance_task_next_run_handler(db))
    registry.register("mark_task_run", make_mark_task_run_handler(db))
    registry.register("update_scheduled_task", make_update_scheduled_task_handler(db))
    registry.register("delete_scheduled_task", make_delete_scheduled_task_handler(db))
    registry.register("record_task_run", make_record_task_run_handler(db))
    registry.register("list_task_runs", make_list_task_runs_handler(db))
    registry.register("create_file_metadata", make_create_file_metadata_handler(db))
    registry.register("list_files", make_list_files_handler(db))
    registry.register("delete_file_metadata", make_delete_file_metadata_handler(db))
    registry.register("get_file_metadata", make_get_file_metadata_handler(db))
    registry.register("save_file_context", make_save_file_context_handler(db))
    registry.register(
        "save_file_context_usage",
        make_save_file_context_usage_handler(db),
    )
    registry.register("get_file_context", make_get_file_context_handler(db))
    registry.register("get_stats", make_get_stats_handler(db))
    registry.register("list_records", make_list_records_handler(db))
    registry.register("list_messages", make_list_messages_handler(db))
    registry.register("count_memory_entries", make_count_memory_entries_handler(db))
    registry.register("mcp_memory_recent", make_mcp_memory_recent_handler(db))
    registry.register("mcp_memory_get", make_mcp_memory_get_handler(db))
    registry.register("mcp_memory_stats", make_mcp_memory_stats_handler(db))
    registry.register("list_audit_log", make_list_audit_log_handler(db))


def _register_auth_and_schema_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
    master_key: bytes,
) -> None:
    """Register auth and schema setup operations."""
    registry.register(
        "get_signing_material",
        make_get_signing_material_handler(db, master_key),
    )
    registry.register(
        "store_password_hash",
        make_store_password_hash_handler(db),
    )
    registry.register(
        "get_password_hash",
        make_get_password_hash_handler(db),
    )
    registry.register("initialize", make_initialize_handler(db))


def _register_health_check(
    registry: OperationRegistry,
    core_db: DatabaseManager,
    runtime_db: DatabaseManager | None = None,
    diagnostics_db: DatabaseManager | None = None,
) -> None:
    """Register the shared DB health check operation."""

    async def db_health_check() -> bool:
        """Check if DB connection is alive."""
        databases = [core_db]
        if runtime_db is not None:
            databases.append(runtime_db)
        if diagnostics_db is not None:
            databases.append(diagnostics_db)
        return all(db.is_open for db in databases)

    registry.register(
        "_health_check",
        create_health_handler(registry, db_health_check),
    )


def build_registry(
    core_db: DatabaseManager,
    database_path: str,
    master_key: bytes,
    runtime_db: DatabaseManager | None = None,
    diagnostics_db: DatabaseManager | None = None,
    diagnostics_retention_days: int = 14,
) -> OperationRegistry:
    """Build the complete operation registry for the DB Proxy.

    Args:
        core_db: Open primary/core DatabaseManager instance.
        database_path: Path to the database file (for backups).
        master_key: Raw 32-byte master key (for signing material).
        runtime_db: Optional runtime DatabaseManager for agent lifecycle state.
        diagnostics_db: Optional diagnostics database for route snapshots.
        diagnostics_retention_days: Number of days to keep route diagnostics.

    Returns:
        Configured OperationRegistry with all operations registered.
    """
    runtime_db = runtime_db or core_db
    registry = OperationRegistry()
    backup_databases = {"core": core_db, "runtime": runtime_db}
    if diagnostics_db is not None:
        backup_databases["diagnostics"] = diagnostics_db
    registry.register(
        "create_backup",
        make_create_backup_handler(backup_databases, database_path),
    )
    _register_message_operations(registry, core_db)
    _register_diagnostic_operations(
        registry,
        core_db,
        diagnostics_db,
        diagnostics_retention_days,
    )
    _register_memory_debug_operations(registry, core_db)
    _register_memory_operations(registry, core_db)
    _register_graph_operations(registry, core_db)
    _register_conversation_and_bridge_operations(registry, core_db)
    _register_permission_operations(registry, core_db, runtime_db)
    _register_integration_operations(registry, core_db)
    _register_misc_db_operations(registry, core_db)
    _register_auth_and_schema_operations(registry, core_db, master_key)
    _register_health_check(registry, core_db, runtime_db, diagnostics_db)
    return registry


def build_allowlist() -> ChannelAllowlist:
    """Build the channel allowlist for tri-socket configuration.

    Returns:
        Configured ChannelAllowlist with Socket A, B, and C rules.
    """
    allowlist = ChannelAllowlist()
    allowlist.set_allowed(CHANNEL_A, SOCKET_A_OPERATIONS | {"_health_check"})
    allowlist.set_allowed(CHANNEL_B, SOCKET_B_OPERATIONS | {"_health_check"})
    allowlist.set_allowed(CHANNEL_C, SOCKET_C_OPERATIONS | {"_health_check"})
    return allowlist
