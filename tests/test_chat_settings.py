"""Tests for per-conversation chat settings storage and DB proxy handlers.

Covers create, list, upsert, cascade delete, and default value behavior
for the chat_settings key-value table.
"""

import asyncio
import secrets

import pytest

from backend.processes.db_proxy.handlers.chat_settings import (
    make_list_chat_settings_handler,
    make_upsert_chat_settings_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


@pytest.fixture
def master_key() -> bytearray:
    """Return a random master key for one test database."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db_path(tmp_path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "chat-settings.db")


@pytest.fixture
def db(db_path: str, master_key: bytearray) -> DatabaseManager:
    """Return an initialized database manager."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    yield manager
    manager.close()


def _insert_conversation(db: DatabaseManager, conversation_id: str) -> None:
    """Insert a minimal conversation row for foreign key constraints."""
    db.execute(
        "INSERT INTO conversations (id, title) VALUES (?, ?)",
        (conversation_id, "Test"),
    )
    db.commit()


class TestChatSettingsSchema:
    """Tests for chat_settings schema initialization."""

    def test_initialize_schema_creates_chat_settings_table(
        self,
        db: DatabaseManager,
    ) -> None:
        """Verify chat_settings table exists after schema initialization."""
        table_names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "chat_settings" in table_names


class TestChatSettingsHandlers:
    """Tests for chat settings list and upsert handlers."""

    def test_list_empty_settings(self, db: DatabaseManager) -> None:
        """Empty dict when no settings exist for a conversation."""
        _insert_conversation(db, "conv-1")
        handler = make_list_chat_settings_handler(db)
        result = asyncio.run(handler({"conversation_id": "conv-1"}))
        assert result == {"settings": {}}

    def test_upsert_and_list(self, db: DatabaseManager) -> None:
        """Upsert stores settings and list returns them."""
        _insert_conversation(db, "conv-1")
        upsert = make_upsert_chat_settings_handler(db)
        list_handler = make_list_chat_settings_handler(db)

        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [
                        {"key": "show_reasoning", "value": "1"},
                        {"key": "show_thinking", "value": "0"},
                    ],
                }
            )
        )

        result = asyncio.run(list_handler({"conversation_id": "conv-1"}))
        assert result["settings"] == {"show_reasoning": "1", "show_thinking": "0"}

    def test_upsert_overwrites_existing_key(self, db: DatabaseManager) -> None:
        """Upserting an existing key updates its value."""
        _insert_conversation(db, "conv-1")
        upsert = make_upsert_chat_settings_handler(db)

        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [{"key": "show_reasoning", "value": "0"}],
                }
            )
        )
        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [{"key": "show_reasoning", "value": "1"}],
                }
            )
        )

        list_handler = make_list_chat_settings_handler(db)
        result = asyncio.run(list_handler({"conversation_id": "conv-1"}))
        assert result["settings"]["show_reasoning"] == "1"

    def test_upsert_returns_full_settings(self, db: DatabaseManager) -> None:
        """Upsert returns all settings for the conversation after update."""
        _insert_conversation(db, "conv-1")
        upsert = make_upsert_chat_settings_handler(db)

        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [{"key": "show_reasoning", "value": "1"}],
                }
            )
        )
        result = asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [{"key": "show_thinking", "value": "1"}],
                }
            )
        )
        assert result["settings"] == {"show_reasoning": "1", "show_thinking": "1"}

    def test_cascade_delete_on_conversation_removal(self, db: DatabaseManager) -> None:
        """Settings are deleted when the parent conversation is removed."""
        _insert_conversation(db, "conv-1")
        upsert = make_upsert_chat_settings_handler(db)

        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [{"key": "show_reasoning", "value": "1"}],
                }
            )
        )

        db.execute("DELETE FROM conversations WHERE id = ?", ("conv-1",))
        db.commit()

        list_handler = make_list_chat_settings_handler(db)
        result = asyncio.run(list_handler({"conversation_id": "conv-1"}))
        assert result["settings"] == {}

    def test_settings_isolated_between_conversations(self, db: DatabaseManager) -> None:
        """Settings for one conversation do not affect another."""
        _insert_conversation(db, "conv-1")
        _insert_conversation(db, "conv-2")
        upsert = make_upsert_chat_settings_handler(db)

        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [{"key": "show_reasoning", "value": "1"}],
                }
            )
        )
        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-2",
                    "entries": [{"key": "show_reasoning", "value": "0"}],
                }
            )
        )

        list_handler = make_list_chat_settings_handler(db)
        r1 = asyncio.run(list_handler({"conversation_id": "conv-1"}))
        r2 = asyncio.run(list_handler({"conversation_id": "conv-2"}))
        assert r1["settings"]["show_reasoning"] == "1"
        assert r2["settings"]["show_reasoning"] == "0"

    def test_list_all_settings(self, db: DatabaseManager) -> None:
        """List without conversation_id returns all settings grouped."""
        _insert_conversation(db, "conv-1")
        _insert_conversation(db, "conv-2")
        upsert = make_upsert_chat_settings_handler(db)

        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-1",
                    "entries": [{"key": "show_reasoning", "value": "1"}],
                }
            )
        )
        asyncio.run(
            upsert(
                {
                    "conversation_id": "conv-2",
                    "entries": [{"key": "show_thinking", "value": "1"}],
                }
            )
        )

        list_handler = make_list_chat_settings_handler(db)
        result = asyncio.run(list_handler({}))
        all_settings = result["all_settings"]
        assert all_settings["conv-1"] == {"show_reasoning": "1"}
        assert all_settings["conv-2"] == {"show_thinking": "1"}
