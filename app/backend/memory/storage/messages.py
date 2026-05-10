"""Message persistence for conversation history."""

import uuid
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import sqlite3


def _message_id() -> str:
    """Return a new message identifier."""
    return str(uuid.uuid4())


def _ensure_conversation_exists(
    db: sqlite3.Connection,
    conversation_id: str,
) -> None:
    """Ensure the target conversation row exists before inserting messages."""
    db.execute(
        "INSERT OR IGNORE INTO conversations (id) VALUES (?)",
        (conversation_id,),
    )


def _message_rows(
    rows: list[tuple[object, object]],
) -> list[tuple[str, str]]:
    """Convert recent message rows to typed role/content tuples."""
    return [(str(role), str(content)) for role, content in rows]


def _recent_message_rows(
    db: sqlite3.Connection,
    conversation_id: str,
    limit: int,
) -> list[tuple[object, object]]:
    """Load the raw recent message rows before role/content normalization."""
    return db.execute(
        "SELECT role, content FROM messages "
        "WHERE conversation_id = ? "
        "ORDER BY created_at ASC "
        "LIMIT ?",
        (conversation_id, limit),
    ).fetchall()


def save_message(
    db: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str,
    channel: str | None = None,
) -> str:
    """Insert a message into the messages table."""
    message_id = _message_id()
    _ensure_conversation_exists(db, conversation_id)
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, channel) "
        "VALUES (?, ?, ?, ?, ?)",
        (message_id, conversation_id, role, content, channel),
    )
    db.commit()
    return message_id


def load_recent_messages(
    db: sqlite3.Connection,
    conversation_id: str,
    limit: int = 50,
) -> list[tuple[str, str]]:
    """Load recent messages for a conversation."""
    rows = _recent_message_rows(db, conversation_id, limit)
    return _message_rows(rows)
