"""Tests for secure deletion with cascade and VACUUM.

Covers conversation deletion cascading to messages and records,
summary deletion, and VACUUM execution.
"""

import secrets

import pytest

from backend.memory.retrieval.fulltext import (
    create_fts_tables,
)
from backend.security.database import DatabaseManager
from backend.security.deletion import delete_conversation
from backend.security.schema import initialize_schema


@pytest.fixture
def del_db(tmp_path):
    """Create a database with schema for deletion tests."""
    db = DatabaseManager(str(tmp_path / "del.db"))
    db.open(bytearray(secrets.token_bytes(32)))
    initialize_schema(db)
    create_fts_tables(db)
    # Create vector table stub for deletion tests
    db.execute(
        "CREATE TABLE IF NOT EXISTS record_embeddings "
        "(record_id TEXT PRIMARY KEY, embedding BLOB)"
    )
    db.commit()
    yield db
    db.close()


def _insert_conversation(db, conv_id):
    """Insert a test conversation with messages and records."""
    db.execute(
        "INSERT INTO conversations (id, title) VALUES (?, ?)",
        (conv_id, "Test"),
    )
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
        (f"msg-{conv_id}", conv_id, "user", "Hello"),
    )
    db.execute(
        "INSERT INTO records "
        "(id, conversation_id, session_id, summary) "
        "VALUES (?, ?, ?, ?)",
        (f"obs-{conv_id}", conv_id, "sess-1", "Summary"),
    )
    db.execute(
        "INSERT INTO record_embeddings (record_id, embedding) VALUES (?, ?)",
        (f"obs-{conv_id}", b"fake"),
    )
    db.commit()


class TestDeleteConversation:
    """Tests for conversation deletion."""

    def test_deletes_conversation(self, del_db):
        """Verify conversation record is removed."""
        _insert_conversation(del_db, "conv-del")
        delete_conversation(del_db, "conv-del")
        cursor = del_db.execute(
            "SELECT count(*) FROM conversations WHERE id = ?",
            ("conv-del",),
        )
        assert cursor.fetchone()[0] == 0

    def test_cascades_to_messages(self, del_db):
        """Verify messages are deleted with conversation."""
        _insert_conversation(del_db, "conv-cas")
        delete_conversation(del_db, "conv-cas")
        cursor = del_db.execute(
            "SELECT count(*) FROM messages WHERE conversation_id = ?",
            ("conv-cas",),
        )
        assert cursor.fetchone()[0] == 0

    def test_deletes_embeddings(self, del_db):
        """Verify record embeddings are deleted."""
        _insert_conversation(del_db, "conv-emb")
        delete_conversation(del_db, "conv-emb")
        cursor = del_db.execute(
            "SELECT count(*) FROM record_embeddings WHERE record_id = ?",
            ("obs-conv-emb",),
        )
        assert cursor.fetchone()[0] == 0


class TestDeleteSummary:
    """Tests for summary deletion."""

