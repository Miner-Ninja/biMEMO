"""Tests for user profile and avatar features.

Covers:
- get_user_profile returns empty profile on empty DB
- store_user_profile → get_user_profile round-trip
- custom_instructions truncation to 2000 chars
- first_name and last_name profile fields
- _build_system_prompt includes User Profile when filled
- _build_system_prompt excludes User Profile when empty
- _build_profile_section skips empty fields
- avatar upload, serve, and delete via DB handlers
"""

import base64
import re
import sqlite3
from unittest.mock import MagicMock

import pytest
from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.concurrency import AgentConcurrency
from backend.processes.db_proxy import (
    make_delete_avatar_handler,
    make_get_avatar_handler,
    make_get_user_profile_handler,
    make_store_avatar_handler,
    make_store_user_profile_handler,
)


class FakeDB:
    """Minimal DatabaseManager stand-in using in-memory SQLite."""

    def __init__(self) -> None:
        """Initialize an in-memory SQLite database."""
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value BLOB NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE avatars ("
            "type TEXT PRIMARY KEY CHECK (type IN ('user', 'ai')), "
            "data BLOB NOT NULL, "
            "mime_type TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))"
            ")"
        )

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement against the in-memory database."""
        return self.connection.execute(sql, params)

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()


@pytest.fixture
def fake_db():
    """Create a FakeDB instance for testing."""
    return FakeDB()


class TestUserProfileDBHandlers:
    """Tests for get_user_profile and store_user_profile handlers."""

    @pytest.mark.asyncio
    async def test_get_empty_profile(self, fake_db):
        """get_user_profile returns empty profile on empty DB."""
        handler = make_get_user_profile_handler(fake_db)
        result = await handler(None)
        assert result == {"profile": {}}

    @pytest.mark.asyncio
    async def test_store_and_get_roundtrip(self, fake_db):
        """store_user_profile then get_user_profile returns the stored data."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)

        await store(
            {
                "display_name": "Alex",
                "timezone": "Europe/Moscow",
                "language": "ru",
                "custom_instructions": "Always reply in Russian",
            }
        )

        result = await get(None)
        profile = result["profile"]
        assert profile["display_name"] == "Alex"
        assert profile["timezone"] == "Europe/Moscow"
        assert profile["language"] == "ru"
        assert profile["custom_instructions"] == "Always reply in Russian"

    @pytest.mark.asyncio
    async def test_store_truncates_custom_instructions(self, fake_db):
        """custom_instructions is truncated to 2000 characters."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)

        long_text = "x" * 3000
        await store({"custom_instructions": long_text})

        result = await get(None)
        assert len(result["profile"]["custom_instructions"]) == 2000

    @pytest.mark.asyncio
    async def test_store_overwrites_existing(self, fake_db):
        """Storing again overwrites the previous profile."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)

        await store({"display_name": "Alice"})
        await store({"display_name": "Bob"})

        result = await get(None)
        assert result["profile"]["display_name"] == "Bob"

    @pytest.mark.asyncio
    async def test_store_rejects_extra_fields(self, fake_db):
        """Extra fields are rejected by Pydantic validation."""
        store = make_store_user_profile_handler(fake_db)
        with pytest.raises(ValueError, match="unknown_field"):
            await store({"display_name": "Alex", "unknown_field": "bad"})

    @pytest.mark.asyncio
    async def test_get_handles_corrupt_json(self, fake_db):
        """get_user_profile returns empty profile on corrupt JSON."""
        fake_db.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            ("user_profile", "not valid json"),
        )
        fake_db.commit()

        handler = make_get_user_profile_handler(fake_db)
        result = await handler(None)
        assert result == {"profile": {}}


def _make_orchestrator(user_profile=None):
    """Create a AgentOrchestrator with test defaults."""
    broker = MagicMock()
    broker.list_schemas.return_value = [
        {"name": "web_search", "description": "Search the web"},
    ]
    return AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda sid, **kwargs: [],
        user_profile=user_profile or {},
    )


class TestSystemPromptUserProfile:
    """Tests for User Profile injection in _build_system_prompt."""

    def test_prompt_requires_three_follow_up_suggestions(self):
        """System prompt requires a 3-question suggestions block on every answer."""
        orchestrator = _make_orchestrator(user_profile={})
        prompt = orchestrator._build_system_prompt()
        assert "```suggestions" in prompt
        assert "End every answer with exactly this format" in prompt
        assert "the same language as the user's latest message" in prompt
        assert "Never write suggestions addressed to the user" in prompt

    def test_empty_profile_no_section(self):
        """Empty profile does not add User Profile section."""
        orchestrator = _make_orchestrator(user_profile={})
        prompt = orchestrator._build_system_prompt()
        assert "## User Profile" not in prompt

    def test_all_empty_strings_no_section(self):
        """Profile with all empty strings does not add section."""
        orchestrator = _make_orchestrator(
            user_profile={
                "display_name": "",
                "timezone": "",
                "language": "",
                "custom_instructions": "",
            }
        )
        prompt = orchestrator._build_system_prompt()
        assert "## User Profile" not in prompt

    def test_filled_profile_includes_section(self):
        """Filled profile adds User Profile section with all fields."""
        orchestrator = _make_orchestrator(
            user_profile={
                "display_name": "Alex",
                "timezone": "Europe/Moscow",
                "language": "ru",
                "custom_instructions": "Be concise",
            }
        )
        prompt = orchestrator._build_system_prompt()
        assert "## User Profile" in prompt
        assert "- Name: Alex" in prompt
        assert "- Timezone: Europe/Moscow" in prompt
        assert "- Language: ru" in prompt
        assert "- Instructions: Be concise" in prompt

    def test_partial_profile_skips_empty(self):
        """Partial profile only includes non-empty fields."""
        orchestrator = _make_orchestrator(
            user_profile={
                "display_name": "Alex",
                "timezone": "",
                "language": "ru",
                "custom_instructions": "",
            }
        )
        prompt = orchestrator._build_system_prompt()
        assert "## User Profile" in prompt
        assert "- Name: Alex" in prompt
        assert "- Language: ru" in prompt
        assert "Timezone" not in prompt
        assert "Instructions" not in prompt

    def test_only_name_set(self):
        """Profile with only display_name produces minimal section."""
        orchestrator = _make_orchestrator(user_profile={"display_name": "Bob"})
        prompt = orchestrator._build_system_prompt()
        assert "## User Profile" in prompt
        assert "- Name: Bob" in prompt


class TestSystemPromptModelIdentity:
    """Tests for LLM model identity injection in _build_system_prompt."""

    def test_model_name_present(self):
        """Prompt contains LLM model line when model_name is set."""
        orchestrator = _make_orchestrator()
        orchestrator.model_name = "qwen/qwen3-14b"
        prompt = orchestrator._build_system_prompt()
        assert "LLM model: qwen/qwen3-14b" in prompt

    def test_model_name_absent(self):
        """Prompt does not contain LLM model line when model_name is empty."""
        orchestrator = _make_orchestrator()
        prompt = orchestrator._build_system_prompt()
        assert "LLM model:" not in prompt

    def test_model_name_between_datetime_and_profile(self):
        """Model line appears after datetime and before User Profile."""
        orchestrator = _make_orchestrator(
            user_profile={
                "display_name": "Alex",
                "timezone": "Europe/Moscow",
            }
        )
        orchestrator.model_name = "qwen/qwen3-14b"
        prompt = orchestrator._build_system_prompt()
        datetime_position = prompt.index("Current datetime:")
        model_position = prompt.index("LLM model:")
        profile_position = prompt.index("## User Profile")
        assert datetime_position < model_position < profile_position


class TestSystemPromptDatetime:
    """Tests for current datetime injection in _build_system_prompt."""

    def test_datetime_with_timezone(self):
        """Prompt contains datetime in user's timezone."""
        orchestrator = _make_orchestrator(user_profile={"timezone": "Europe/Moscow"})
        prompt = orchestrator._build_system_prompt()
        match = re.search(r"Current datetime: (.+)", prompt)
        assert match is not None
        # Moscow is UTC+3
        assert "+0300" in match.group(1)

    def test_datetime_without_timezone(self):
        """Prompt contains datetime in UTC when no timezone set."""
        orchestrator = _make_orchestrator(user_profile={})
        prompt = orchestrator._build_system_prompt()
        match = re.search(r"Current datetime: (.+)", prompt)
        assert match is not None
        assert "+0000" in match.group(1)

    def test_datetime_invalid_timezone_fallback(self, caplog):
        """Invalid timezone falls back to UTC with warning."""
        orchestrator = _make_orchestrator(user_profile={"timezone": "Invalid/Zone"})
        with caplog.at_level("WARNING"):
            prompt = orchestrator._build_system_prompt()
        match = re.search(r"Current datetime: (.+)", prompt)
        assert match is not None
        assert "+0000" in match.group(1)
        assert "Invalid timezone" in caplog.text

    def test_datetime_before_profile(self):
        """Datetime line appears before User Profile section."""
        orchestrator = _make_orchestrator(
            user_profile={
                "display_name": "Alex",
                "timezone": "Europe/Moscow",
            }
        )
        prompt = orchestrator._build_system_prompt()
        datetime_position = prompt.index("Current datetime:")
        profile_position = prompt.index("## User Profile")
        assert datetime_position < profile_position


# --- Small PNG for avatar tests (1x1 pixel, valid format) ---
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
TINY_PNG_B64 = base64.b64encode(TINY_PNG).decode("ascii")


class TestAvatarUpload:
    """Tests for avatar store handler (upload)."""

    @pytest.mark.asyncio
    async def test_store_user_avatar(self, fake_db):
        """Store user avatar and verify it exists in the table."""
        store = make_store_avatar_handler(fake_db)
        result = await store(
            {
                "avatar_type": "user",
                "data": TINY_PNG_B64,
                "mime_type": "image/png",
            }
        )
        assert result == {"ok": True}
        row = fake_db.execute(
            "SELECT type, mime_type FROM avatars WHERE type = 'user'"
        ).fetchone()
        assert row is not None
        assert row[1] == "image/png"

    @pytest.mark.asyncio
    async def test_store_ai_avatar(self, fake_db):
        """Store AI avatar and verify it exists in the table."""
        store = make_store_avatar_handler(fake_db)
        result = await store(
            {
                "avatar_type": "ai",
                "data": TINY_PNG_B64,
                "mime_type": "image/webp",
            }
        )
        assert result == {"ok": True}
        row = fake_db.execute(
            "SELECT type, mime_type FROM avatars WHERE type = 'ai'"
        ).fetchone()
        assert row is not None
        assert row[1] == "image/webp"

    @pytest.mark.asyncio
    async def test_store_replaces_existing(self, fake_db):
        """Uploading again replaces the previous avatar."""
        store = make_store_avatar_handler(fake_db)
        await store(
            {
                "avatar_type": "user",
                "data": TINY_PNG_B64,
                "mime_type": "image/png",
            }
        )
        new_data = base64.b64encode(b"new-image-data").decode("ascii")
        await store(
            {
                "avatar_type": "user",
                "data": new_data,
                "mime_type": "image/jpeg",
            }
        )
        row = fake_db.execute(
            "SELECT mime_type FROM avatars WHERE type = 'user'"
        ).fetchone()
        assert row[0] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_store_rejects_invalid_type(self, fake_db):
        """Invalid avatar type raises ValueError."""
        store = make_store_avatar_handler(fake_db)
        with pytest.raises(ValueError, match="Invalid avatar type"):
            await store(
                {
                    "avatar_type": "other",
                    "data": TINY_PNG_B64,
                    "mime_type": "image/png",
                }
            )


class TestAvatarServe:
    """Tests for avatar get handler (serve)."""

    @pytest.mark.asyncio
    async def test_get_returns_avatar_data(self, fake_db):
        """Get avatar returns base64-encoded data and mime type."""
        store = make_store_avatar_handler(fake_db)
        get = make_get_avatar_handler(fake_db)
        await store(
            {
                "avatar_type": "user",
                "data": TINY_PNG_B64,
                "mime_type": "image/png",
            }
        )
        result = await get({"avatar_type": "user"})
        avatar = result["avatar"]
        assert avatar is not None
        assert avatar["mime_type"] == "image/png"
        assert base64.b64decode(avatar["data"]) == TINY_PNG

    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_set(self, fake_db):
        """Get avatar returns None when no avatar is stored."""
        get = make_get_avatar_handler(fake_db)
        result = await get({"avatar_type": "user"})
        assert result == {"avatar": None}

    @pytest.mark.asyncio
    async def test_get_rejects_invalid_type(self, fake_db):
        """Invalid avatar type raises ValueError."""
        get = make_get_avatar_handler(fake_db)
        with pytest.raises(ValueError, match="Invalid avatar type"):
            await get({"avatar_type": "bad"})


class TestAvatarDelete:
    """Tests for avatar delete handler."""

    @pytest.mark.asyncio
    async def test_delete_removes_avatar(self, fake_db):
        """Delete removes the stored avatar."""
        store = make_store_avatar_handler(fake_db)
        delete = make_delete_avatar_handler(fake_db)
        get = make_get_avatar_handler(fake_db)
        await store(
            {
                "avatar_type": "ai",
                "data": TINY_PNG_B64,
                "mime_type": "image/png",
            }
        )
        result = await delete({"avatar_type": "ai"})
        assert result == {"ok": True}
        get_result = await get({"avatar_type": "ai"})
        assert get_result == {"avatar": None}

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_ok(self, fake_db):
        """Deleting a non-existent avatar does not raise."""
        delete = make_delete_avatar_handler(fake_db)
        result = await delete({"avatar_type": "user"})
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_delete_rejects_invalid_type(self, fake_db):
        """Invalid avatar type raises ValueError."""
        delete = make_delete_avatar_handler(fake_db)
        with pytest.raises(ValueError, match="Invalid avatar type"):
            await delete({"avatar_type": "invalid"})


class TestExtendedProfileFields:
    """Tests for first_name and last_name profile fields."""

    @pytest.mark.asyncio
    async def test_store_and_get_name_fields(self, fake_db):
        """first_name and last_name are stored and returned."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)
        await store(
            {
                "first_name": "John",
                "last_name": "Doe",
                "display_name": "JD",
            }
        )
        result = await get(None)
        profile = result["profile"]
        assert profile["first_name"] == "John"
        assert profile["last_name"] == "Doe"
        assert profile["display_name"] == "JD"

    @pytest.mark.asyncio
    async def test_name_fields_default_to_empty(self, fake_db):
        """Omitted name fields default to empty strings."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)
        await store({"display_name": "Alex"})
        result = await get(None)
        profile = result["profile"]
        assert profile["first_name"] == ""
        assert profile["last_name"] == ""

    @pytest.mark.asyncio
    async def test_name_fields_overwrite(self, fake_db):
        """Name fields are overwritten on subsequent store."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)
        await store({"first_name": "Alice", "last_name": "Smith"})
        await store({"first_name": "Bob", "last_name": "Jones"})
        result = await get(None)
        profile = result["profile"]
        assert profile["first_name"] == "Bob"
        assert profile["last_name"] == "Jones"


class TestAssistantNameProfileField:
    """Tests for assistant_name profile field."""

    @pytest.mark.asyncio
    async def test_store_and_get_assistant_name(self, fake_db):
        """assistant_name is stored and returned."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)
        await store({"assistant_name": "Aria", "display_name": "Alex"})
        result = await get(None)
        profile = result["profile"]
        assert profile["assistant_name"] == "Aria"
        assert profile["display_name"] == "Alex"

    @pytest.mark.asyncio
    async def test_assistant_name_defaults_to_empty(self, fake_db):
        """Omitted assistant_name defaults to empty string."""
        store = make_store_user_profile_handler(fake_db)
        get = make_get_user_profile_handler(fake_db)
        await store({"display_name": "Alex"})
        result = await get(None)
        assert result["profile"]["assistant_name"] == ""


class TestAssistantNameInPrompt:
    """Tests for assistant_name in system prompt."""

    def test_assistant_name_present_in_prompt(self):
        """Profile with assistant_name includes it in system prompt."""
        orchestrator = _make_orchestrator(
            user_profile={"display_name": "Alex", "assistant_name": "Aria"}
        )
        prompt = orchestrator._build_system_prompt()
        assert "## User Profile" in prompt
        assert "- Assistant name: Aria" in prompt

    def test_assistant_name_absent_from_prompt(self):
        """Empty assistant_name is not included in system prompt."""
        orchestrator = _make_orchestrator(
            user_profile={"display_name": "Alex", "assistant_name": ""}
        )
        prompt = orchestrator._build_system_prompt()
        assert "Assistant name" not in prompt

    def test_assistant_name_missing_from_prompt(self):
        """Profile without assistant_name does not include it."""
        orchestrator = _make_orchestrator(user_profile={"display_name": "Alex"})
        prompt = orchestrator._build_system_prompt()
        assert "Assistant name" not in prompt
