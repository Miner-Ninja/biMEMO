"""Channel constants, operation sets, and registry/allowlist builders."""

from backend.ipc.db_proxy import (
    ChannelAllowlist,
    OperationRegistry,
    create_health_handler,
)
from backend.security.database import DatabaseManager

from .handlers.admin import (
    make_count_memory_entries_handler,
    make_delete_avatar_handler,
    make_get_avatar_handler,
    make_get_password_hash_handler,
    make_get_recent_rate_limit_calls_handler,
    make_get_signing_material_handler,
    make_get_stats_handler,
    make_get_user_profile_handler,
    make_initialize_handler,
    make_list_audit_log_handler,
    make_list_messages_handler,
    make_list_records_handler,
    make_list_summaries_full_handler,
    make_record_rate_limit_call_handler,
    make_store_avatar_handler,
    make_store_password_hash_handler,
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
    make_update_conversation_processing_state_handler,
    make_update_conversation_title_handler,
)
from .handlers.core import (
    make_count_distillations_handler,
    make_fetch_unembedded_records_handler,
    make_fetch_untainted_records_handler,
    make_fulltext_search_handler,
    make_init_vector_table_handler,
    make_list_summaries_handler,
    make_load_context_handler,
    make_load_recent_messages_handler,
    make_mark_tainted_handler,
    make_merge_summary_sources_handler,
    make_save_conversation_summary_handler,
    make_save_message_handler,
    make_save_record_handler,
    make_save_summary_handler,
    make_store_embedding_handler,
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
    "load_context",
    "write_audit",
    "verify_chain",
    "save_record",
    "mark_tainted",
    "store_embedding",
    "fetch_untainted_records",
    "save_summary",
    "merge_summary_sources",
    "list_summaries",
    "count_distillations",
    "list_summaries_full",
    "create_conversation",
    "list_conversations",
    "delete_conversation",
    "update_conversation_title",
    "update_conversation_processing_state",
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
}

# Socket B adds admin, auth, schema (no credentials — those go via Socket C)
SOCKET_B_ONLY_OPERATIONS = {
    "create_conversation",
    "get_stats",
    "get_signing_material",
    "store_password_hash",
    "get_password_hash",
    "initialize",
    "get_user_profile",
    "store_user_profile",
    "get_avatar",
    "store_avatar",
    "delete_avatar",
    "get_due_tasks",
    "advance_task_next_run",
    "mark_task_run",
    "record_task_run",
    "list_task_runs",
    "list_records",
    "list_summaries_full",
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
}

SOCKET_B_OPERATIONS = SOCKET_B_ONLY_OPERATIONS | {
    "write_audit",
    "list_summaries",
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


def _register_memory_operations(
    registry: OperationRegistry,
    db: DatabaseManager,
) -> None:
    """Register records, summaries, and reminders operations."""
    registry.register("save_record", make_save_record_handler(db))
    registry.register("mark_tainted", make_mark_tainted_handler(db))
    registry.register("store_embedding", make_store_embedding_handler(db))
    registry.register(
        "fetch_untainted_records",
        make_fetch_untainted_records_handler(db),
    )
    registry.register("save_summary", make_save_summary_handler(db))
    registry.register(
        "merge_summary_sources",
        make_merge_summary_sources_handler(db),
    )
    registry.register("list_summaries", make_list_summaries_handler(db))
    registry.register("count_distillations", make_count_distillations_handler(db))
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
    registry.register("list_summaries_full", make_list_summaries_full_handler(db))
    registry.register("list_messages", make_list_messages_handler(db))
    registry.register("count_memory_entries", make_count_memory_entries_handler(db))
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
    db: DatabaseManager,
) -> None:
    """Register the shared DB health check operation."""

    async def db_health_check() -> bool:
        """Check if DB connection is alive."""
        return db.is_open

    registry.register(
        "_health_check",
        create_health_handler(registry, db_health_check),
    )


def build_registry(
    db: DatabaseManager,
    database_path: str,
    master_key: bytes,
    diagnostics_db: DatabaseManager | None = None,
    diagnostics_retention_days: int = 14,
) -> OperationRegistry:
    """Build the complete operation registry for the DB Proxy.

    Args:
        db: Open DatabaseManager instance.
        database_path: Path to the database file (for backups).
        master_key: Raw 32-byte master key (for signing material).
        diagnostics_db: Optional diagnostics database for route snapshots.
        diagnostics_retention_days: Number of days to keep route diagnostics.

    Returns:
        Configured OperationRegistry with all operations registered.
    """
    del database_path
    registry = OperationRegistry()
    _register_message_operations(registry, db)
    _register_diagnostic_operations(
        registry,
        db,
        diagnostics_db,
        diagnostics_retention_days,
    )
    _register_memory_debug_operations(registry, db)
    _register_memory_operations(registry, db)
    _register_conversation_and_bridge_operations(registry, db)
    _register_integration_operations(registry, db)
    _register_misc_db_operations(registry, db)
    _register_auth_and_schema_operations(registry, db, master_key)
    _register_health_check(registry, db)
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
