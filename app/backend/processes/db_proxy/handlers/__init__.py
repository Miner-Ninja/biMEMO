"""DB proxy handler factories subpackage.

Re-exports all handler factories from core.py, which itself
re-exports from the domain-specific handler modules (memory,
message, admin, etc.).
"""

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
    "make_fetch_unembedded_records_handler",
    "make_fulltext_search_handler",
    "make_init_vector_table_handler",
    "make_load_context_handler",
    "make_load_recent_messages_handler",
    "make_prune_unlinked_records_handler",
    "make_save_conversation_summary_handler",
    "make_save_message_handler",
    "make_save_record_handler",
    "make_store_embedding_handler",
    "make_verify_chain_handler",
    "make_write_audit_handler",
]
