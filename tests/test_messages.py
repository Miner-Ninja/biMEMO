"""Tests for message persistence (save and load).

Covers save_message UUID generation, field correctness,
load_recent_messages ordering, limit, and empty results.
"""

import sqlite3
import uuid

import pytest

from backend.memory.storage.messages import load_recent_messages, save_message


@pytest.fixture
def db(tmp_path):
    """Create an in-memory database with messages table."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE messages ("
        "  id TEXT PRIMARY KEY,"
        "  conversation_id TEXT NOT NULL,"
        "  role TEXT NOT NULL,"
        "  content TEXT NOT NULL,"
        "  channel TEXT,"
        "  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))"
        ")"
    )
    conn.commit()
    return conn


class TestSaveMessage:
    """Tests for save_message function."""

    def test_inserts_row(self, db):
        """Verify message is inserted with correct fields."""
        msg_id = save_message(db, "conv-1", "user", "hello", channel="web")
        row = db.execute(
            "SELECT id, conversation_id, role, content, channel FROM messages"
        ).fetchone()
        assert row is not None
        assert row[0] == msg_id
        assert row[1] == "conv-1"
        assert row[2] == "user"
        assert row[3] == "hello"
        assert row[4] == "web"

    def test_generates_uuid(self, db):
        """Verify returned id is a valid UUID."""
        msg_id = save_message(db, "conv-1", "user", "test")
        parsed = uuid.UUID(msg_id)
        assert str(parsed) == msg_id

    def test_channel_defaults_to_none(self, db):
        """Verify channel is None when not provided."""
        save_message(db, "conv-1", "user", "test")
        row = db.execute("SELECT channel FROM messages").fetchone()
        assert row[0] is None

    def test_multiple_messages(self, db):
        """Verify multiple messages can be saved."""
        save_message(db, "conv-1", "user", "first")
        save_message(db, "conv-1", "assistant", "second")
        count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert count == 2


class TestLoadRecentMessages:
    """Tests for load_recent_messages function."""

    def test_returns_ordered_by_created_at(self, db):
        """Verify messages are returned oldest-first."""
        save_message(db, "conv-1", "user", "first")
        save_message(db, "conv-1", "assistant", "second")
        save_message(db, "conv-1", "user", "third")
        result = load_recent_messages(db, "conv-1")
        assert [r[1] for r in result] == ["first", "second", "third"]

    def test_returns_role_and_content(self, db):
        """Verify tuples contain (role, content)."""
        save_message(db, "conv-1", "user", "hello")
        result = load_recent_messages(db, "conv-1")
        assert result == [("user", "hello")]

    def test_respects_limit(self, db):
        """Verify limit caps the number of messages."""
        for i in range(10):
            save_message(db, "conv-1", "user", f"msg-{i}")
        result = load_recent_messages(db, "conv-1", limit=3)
        assert len(result) == 3

    def test_empty_conversation(self, db):
        """Verify empty list for nonexistent conversation."""
        result = load_recent_messages(db, "no-such-conv")
        assert result == []

    def test_filters_by_conversation_id(self, db):
        """Verify only messages from specified conversation are returned."""
        save_message(db, "conv-1", "user", "in conv 1")
        save_message(db, "conv-2", "user", "in conv 2")
        result = load_recent_messages(db, "conv-1")
        assert len(result) == 1
        assert result[0][1] == "in conv 1"
