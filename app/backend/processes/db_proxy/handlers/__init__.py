"""DB proxy handler factories subpackage.

Re-exports all handler factories from core.py, which itself
re-exports from the domain-specific handler modules (memory,
message, admin, etc.).
"""

from .agent_turn import (
    make_append_agent_turn_event_handler,
    make_cancel_agent_turn_handler,
    make_claim_agent_invocation_handler,
    make_commit_agent_turn_finalization_handler,
    make_complete_agent_invocation_handler,
    make_create_agent_turn_handler,
    make_decide_agent_confirmation_handler,
    make_get_agent_turn_handler,
    make_list_unprojected_agent_turn_events_handler,
    make_mark_agent_turn_event_projected_handler,
    make_upsert_agent_tool_batch_handler,
    make_upsert_agent_turn_obligations_handler,
)
from .core import (
    make_fetch_unembedded_records_handler,
    make_fulltext_search_handler,
    make_init_vector_table_handler,
    make_load_context_handler,
    make_load_recent_messages_handler,
    make_prune_unlinked_records_handler,
    make_save_conversation_summary_handler,
    make_save_message_handler,
    make_save_record_handler,
    make_store_embedding_handler,
    make_verify_chain_handler,
    make_write_audit_handler,
)


__all__ = [
    "make_append_agent_turn_event_handler",
    "make_cancel_agent_turn_handler",
    "make_claim_agent_invocation_handler",
    "make_commit_agent_turn_finalization_handler",
    "make_complete_agent_invocation_handler",
    "make_create_agent_turn_handler",
    "make_decide_agent_confirmation_handler",
    "make_fetch_unembedded_records_handler",
    "make_fulltext_search_handler",
    "make_get_agent_turn_handler",
    "make_init_vector_table_handler",
    "make_list_unprojected_agent_turn_events_handler",
    "make_load_context_handler",
    "make_load_recent_messages_handler",
    "make_mark_agent_turn_event_projected_handler",
    "make_prune_unlinked_records_handler",
    "make_save_conversation_summary_handler",
    "make_save_message_handler",
    "make_save_record_handler",
    "make_store_embedding_handler",
    "make_upsert_agent_tool_batch_handler",
    "make_upsert_agent_turn_obligations_handler",
    "make_verify_chain_handler",
    "make_write_audit_handler",
]
