"""Secure deletion with cascade delete and VACUUM.

Implements secure data removal by cascading deletes through the entire
chain: messages, records, embeddings, and FTS5 indexes.
Runs VACUUM after deletion to reclaim space and ensure deleted data
is overwritten in the database file.
"""

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)


def _delete_conversation_embeddings(
    db: DatabaseManager,
    conversation_id: str,
) -> None:
    """Delete vector embeddings tied to one conversation."""
    db.execute(
        "DELETE FROM record_embeddings WHERE record_id IN "
        "(SELECT id FROM records WHERE conversation_id = ?)",
        (conversation_id,),
    )


def delete_conversation(db: DatabaseManager, conversation_id: str) -> None:
    """Delete a conversation and all associated data.

    Cascade deletes messages, records, summaries linked via
    record source IDs, vector embeddings, and FTS5 index entries.
    Runs VACUUM to overwrite freed pages.

    Args:
        db: An open DatabaseManager instance.
        conversation_id: ID of the conversation to delete.
    """
    _delete_conversation_embeddings(db, conversation_id)
    db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    db.commit()

    _vacuum(db)
    logger.info("Conversation %s securely deleted", conversation_id)


def _vacuum(db: DatabaseManager) -> None:
    """Run VACUUM to reclaim space and overwrite deleted data.

    Args:
        db: An open DatabaseManager instance.
    """
    db.execute("VACUUM")
    logger.info("Database VACUUM completed")
