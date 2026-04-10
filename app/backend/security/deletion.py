"""Secure deletion with cascade delete and VACUUM.

Implements secure data removal by cascading deletes through the entire
chain: messages, records, summaries, embeddings, and FTS5 indexes.
Runs VACUUM after deletion to reclaim space and ensure deleted data
is overwritten in the database file.
"""

import logging

from backend.memory.retrieval.fulltext import delete_summary_index
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


def _summary_rowid(db: DatabaseManager, summary_id: str) -> int | None:
    """Return the FTS rowid for one summary when it exists."""
    row = db.execute(
        "SELECT rowid FROM summaries WHERE id = ?",
        (summary_id,),
    ).fetchone()
    if row is None:
        return None
    return row[0]


def _delete_summary_row(db: DatabaseManager, summary_id: str) -> None:
    """Delete one summary row from the main summaries table."""
    db.execute("DELETE FROM summaries WHERE id = ?", (summary_id,))


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


def delete_reflection(db: DatabaseManager, summary_id: str) -> None:
    """Delete a single summary.

    Args:
        db: An open DatabaseManager instance.
        summary_id: ID of the summary to delete.
    """
    rowid = _summary_rowid(db, summary_id)
    if rowid is None:
        return
    _delete_summary_row(db, summary_id)
    delete_summary_index(db, rowid)
    db.commit()
    logger.info("Summary %s deleted", summary_id)


def _vacuum(db: DatabaseManager) -> None:
    """Run VACUUM to reclaim space and overwrite deleted data.

    Args:
        db: An open DatabaseManager instance.
    """
    db.execute("VACUUM")
    logger.info("Database VACUUM completed")
