"""Tests for persisted chat bridge tables and DB proxy handlers."""

import asyncio
import secrets

import pytest

from backend.processes.db_proxy.handlers.chat_bridge import (
    make_create_chat_bridge_link_handler,
    make_create_chat_bridge_message_link_handler,
    make_disable_chat_bridge_link_handler,
    make_get_chat_bridge_by_external_target_handler,
    make_get_chat_bridge_for_conversation_handler,
    make_get_chat_bridge_message_link_by_external_message_handler,
    make_get_chat_bridge_message_link_by_message_id_handler,
    make_list_chat_bridge_links_handler,
    make_list_chat_bridge_message_links_handler,
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
    return str(tmp_path / "chat-bridge.db")


@pytest.fixture
def db(db_path: str, master_key: bytearray) -> DatabaseManager:
    """Return an initialized database manager."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    yield manager
    manager.close()


class TestChatBridgeSchema:
    """Tests for chat-bridge schema initialization."""

    def test_initialize_schema_creates_chat_bridge_tables(
        self,
        db: DatabaseManager,
    ) -> None:
        """Verify chat bridge tables exist after schema initialization."""
        table_names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "chat_bridge_links" in table_names
        assert "chat_bridge_message_links" in table_names


class TestChatBridgeHandlers:
    """Tests for stored chat bridge links and message mappings."""

    def test_create_and_get_chat_bridge_link_round_trip(
        self,
        db: DatabaseManager,
    ) -> None:
        """Verify bridge links can be created and loaded by conversation."""
        db.execute(
            "INSERT INTO conversations (id, title) VALUES (?, ?)",
            ("conversation-1", "Ops room"),
        )
        db.commit()
        create_handler = make_create_chat_bridge_link_handler(db)
        get_handler = make_get_chat_bridge_for_conversation_handler(db)

        create_result = asyncio.run(
            create_handler(
                {
                    "conversation_id": "conversation-1",
                    "provider": "discord",
                    "external_space_id": "guild-1",
                    "external_channel_id": "channel-1",
                    "created_by": "admin-user",
                    "delivery_mode": "bot_only",
                    "persona_mode": "neutral",
                    "persona_user_id": "",
                }
            )
        )

        stored_link = create_result["chat_bridge_link"]
        assert stored_link["provider"] == "discord"
        assert stored_link["sync_direction"] == "bidirectional"
        assert stored_link["delivery_mode"] == "bot_only"
        assert stored_link["persona_mode"] == "neutral"
        round_trip = asyncio.run(get_handler({"conversation_id": "conversation-1"}))[
            "chat_bridge_link"
        ]
        assert round_trip is not None
        assert round_trip["id"] == stored_link["id"]
        assert round_trip["external_channel_id"] == "channel-1"

    def test_get_chat_bridge_by_external_target_finds_stored_link(
        self,
        db: DatabaseManager,
    ) -> None:
        """Verify provider-side identifiers resolve back to one bridge link."""
        db.execute(
            "INSERT INTO conversations (id, title) VALUES (?, ?)",
            ("conversation-1", "Ops room"),
        )
        db.execute(
            "INSERT INTO chat_bridge_links ("
            "id, conversation_id, provider, external_space_id, "
            "external_channel_id, created_by, delivery_mode, persona_mode, "
            "persona_user_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bridge-1",
                "conversation-1",
                "discord",
                "guild-1",
                "channel-1",
                "admin-user",
                "bot_only",
                "neutral",
                "",
            ),
        )
        db.commit()
        get_handler = make_get_chat_bridge_by_external_target_handler(db)

        result = asyncio.run(
            get_handler(
                {
                    "provider": "discord",
                    "external_space_id": "guild-1",
                    "external_channel_id": "channel-1",
                }
            )
        )

        assert result["chat_bridge_link"] is not None
        assert result["chat_bridge_link"]["conversation_id"] == "conversation-1"

    def test_list_and_disable_chat_bridge_links(
        self,
        db: DatabaseManager,
    ) -> None:
        """Verify bridge links can be listed and disabled."""
        db.execute(
            "INSERT INTO conversations (id, title) VALUES (?, ?)",
            ("conversation-1", "Ops room"),
        )
        db.execute(
            "INSERT INTO chat_bridge_links ("
            "id, conversation_id, provider, external_space_id, "
            "external_channel_id, created_by, delivery_mode, persona_mode, "
            "persona_user_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bridge-1",
                "conversation-1",
                "discord",
                "guild-1",
                "channel-1",
                "admin-user",
                "bot_only",
                "neutral",
                "",
            ),
        )
        db.commit()
        list_handler = make_list_chat_bridge_links_handler(db)
        disable_handler = make_disable_chat_bridge_link_handler(db)

        listed = asyncio.run(
            list_handler({"provider": "discord", "limit": 10, "offset": 0})
        )["chat_bridge_links"]
        assert len(listed) == 1
        assert listed[0]["status"] == "active"

        disable_result = asyncio.run(disable_handler({"bridge_link_id": "bridge-1"}))
        assert disable_result["updated"] is True
        updated_status = db.execute(
            "SELECT status FROM chat_bridge_links WHERE id = ?",
            ("bridge-1",),
        ).fetchone()[0]
        assert updated_status == "disabled"

    def test_create_and_get_chat_bridge_message_link_round_trip(
        self,
        db: DatabaseManager,
    ) -> None:
        """Verify bridge message mappings can be created and read back."""
        db.execute(
            "INSERT INTO conversations (id, title) VALUES (?, ?)",
            ("conversation-1", "Ops room"),
        )
        db.execute(
            "INSERT INTO chat_bridge_links ("
            "id, conversation_id, provider, external_space_id, "
            "external_channel_id, created_by, delivery_mode, persona_mode, "
            "persona_user_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bridge-1",
                "conversation-1",
                "discord",
                "guild-1",
                "channel-1",
                "admin-user",
                "bot_only",
                "neutral",
                "",
            ),
        )
        db.commit()
        create_handler = make_create_chat_bridge_message_link_handler(db)
        get_handler = make_get_chat_bridge_message_link_by_external_message_handler(db)
        get_by_message_id_handler = (
            make_get_chat_bridge_message_link_by_message_id_handler(db)
        )
        list_handler = make_list_chat_bridge_message_links_handler(db)

        create_result = asyncio.run(
            create_handler(
                {
                    "bridge_link_id": "bridge-1",
                    "message_id": "web-message-1",
                    "external_message_id": "discord-message-1",
                    "direction": "outbound",
                }
            )
        )

        stored_mapping = create_result["chat_bridge_message_link"]
        assert stored_mapping["direction"] == "outbound"
        round_trip = asyncio.run(
            get_handler(
                {
                    "bridge_link_id": "bridge-1",
                    "external_message_id": "discord-message-1",
                }
            )
        )["chat_bridge_message_link"]
        assert round_trip is not None
        assert round_trip["id"] == stored_mapping["id"]
        assert round_trip["message_id"] == "web-message-1"
        by_message_id = asyncio.run(
            get_by_message_id_handler(
                {
                    "bridge_link_id": "bridge-1",
                    "message_id": "web-message-1",
                }
            )
        )["chat_bridge_message_link"]
        assert by_message_id is not None
        assert by_message_id["external_message_id"] == "discord-message-1"
        listed = asyncio.run(
            list_handler(
                {
                    "bridge_link_id": "bridge-1",
                    "direction": "outbound",
                    "limit": 10,
                    "offset": 0,
                }
            )
        )["chat_bridge_message_links"]
        assert len(listed) == 1
        assert listed[0]["message_id"] == "web-message-1"
