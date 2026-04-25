"""Tests for the db_proxy security-profile handlers and seed.

Covers:

- The schema seeding migration installs the three built-ins and
  sets ``personal`` as the active profile, idempotently.
- list / get / create / update / delete handlers round-trip
  through the database.
- Built-in profiles reject every mutation
  (BuiltinProtectedError).
- Locked-key payloads are rejected at the handler layer too.
- delete-of-active falls back to ``personal``.
"""

import asyncio
import json
import secrets

import pytest
from backend.processes.db_proxy.handlers.security_profiles import (
    make_create_security_profile_handler,
    make_delete_security_profile_handler,
    make_get_active_security_profile_handler,
    make_get_security_profile_handler,
    make_list_security_profiles_handler,
    make_set_active_security_profile_handler,
    make_update_security_profile_handler,
)
from backend.security.database import DatabaseManager
from backend.security.profiles.models import (
    BuiltinProtectedError,
    LockedSettingError,
    ProfileNotFoundError,
)
from backend.security.schema import initialize_schema


@pytest.fixture
def master_key() -> bytearray:
    """Return a fresh random master key per test database."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db_path(tmp_path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "security-profiles.db")


@pytest.fixture
def db(db_path: str, master_key: bytearray):
    """Return an opened DatabaseManager with the schema and seed applied."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    yield manager
    manager.close()


class TestSeedMigration:
    """Cover the idempotent seed step in initialize_schema."""

    def test_seeds_three_builtins(self, db: DatabaseManager) -> None:
        """initialize_schema inserts paranoid/personal/developer."""
        rows = db.execute(
            "SELECT id, is_builtin FROM security_profiles ORDER BY id"
        ).fetchall()
        assert [(row[0], bool(row[1])) for row in rows] == [
            ("developer", True),
            ("paranoid", True),
            ("personal", True),
        ]

    def test_personal_is_active_by_default(self, db: DatabaseManager) -> None:
        """app_settings.active_profile_id is set to personal on first run."""
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'active_profile_id'"
        ).fetchone()
        assert row is not None
        assert row[0] == "personal"

    def test_seed_is_idempotent(
        self,
        db: DatabaseManager,
        master_key: bytearray,
        db_path: str,
    ) -> None:
        """Re-running initialize_schema does not duplicate built-ins."""
        # Run the seed a second time on the already-initialized database.
        initialize_schema(db)
        count = db.execute(
            "SELECT COUNT(*) FROM security_profiles WHERE is_builtin = 1"
        ).fetchone()[0]
        assert count == 3


class TestHandlers:
    """Cover the seven async DB-proxy handlers."""

    def test_list_returns_three_builtins(self, db: DatabaseManager) -> None:
        """list_security_profiles returns the three seeded built-ins."""
        handler = make_list_security_profiles_handler(db)
        result = asyncio.run(handler(None))
        ids = sorted(profile["id"] for profile in result["profiles"])
        assert ids == ["developer", "paranoid", "personal"]

    def test_get_active_returns_personal(
        self,
        db: DatabaseManager,
    ) -> None:
        """get_active_security_profile points at personal by default."""
        handler = make_get_active_security_profile_handler(db)
        result = asyncio.run(handler(None))
        assert result["active_profile_id"] == "personal"
        assert result["profile"]["id"] == "personal"

    def test_get_unknown_id_raises(self, db: DatabaseManager) -> None:
        """get_security_profile raises ProfileNotFoundError for missing ids."""
        handler = make_get_security_profile_handler(db)
        with pytest.raises(ProfileNotFoundError):
            asyncio.run(handler({"id": "ghost"}))

    def test_create_clones_from_developer(
        self,
        db: DatabaseManager,
    ) -> None:
        """Create with base_profile_id seeds settings from the source profile."""
        handler = make_create_security_profile_handler(db)
        result = asyncio.run(
            handler(
                {
                    "name": "my-dev",
                    "description": "Custom",
                    "base_profile_id": "developer",
                },
            ),
        )
        profile = result["profile"]
        assert profile["is_builtin"] is False
        assert profile["settings"]["agent_limits.tools.file_write_per_loop"] == 30

    def test_create_with_override_merges(
        self,
        db: DatabaseManager,
    ) -> None:
        """settings_overrides merge on top of the base profile settings."""
        handler = make_create_security_profile_handler(db)
        result = asyncio.run(
            handler(
                {
                    "name": "my-mix",
                    "base_profile_id": "developer",
                    "settings_overrides": {
                        "agent_limits.iterations.max": 150,
                    },
                },
            ),
        )
        assert result["profile"]["settings"]["agent_limits.iterations.max"] == 150
        assert (
            result["profile"]["settings"]["agent_limits.tools.file_write_per_loop"]
            == 30
        )

    def test_create_rejects_locked_key(self, db: DatabaseManager) -> None:
        """settings_overrides cannot include locked catalog entries."""
        handler = make_create_security_profile_handler(db)
        with pytest.raises(LockedSettingError):
            asyncio.run(
                handler(
                    {
                        "name": "bad",
                        "settings_overrides": {
                            "network.block_cloud_metadata": False,
                        },
                    },
                ),
            )

    def test_update_builtin_rejected(self, db: DatabaseManager) -> None:
        """Updating a built-in profile raises BuiltinProtectedError."""
        handler = make_update_security_profile_handler(db)
        with pytest.raises(BuiltinProtectedError):
            asyncio.run(
                handler({"id": "personal", "description": "hacked"}),
            )

    def test_delete_builtin_rejected(self, db: DatabaseManager) -> None:
        """Deleting a built-in profile raises BuiltinProtectedError."""
        handler = make_delete_security_profile_handler(db)
        with pytest.raises(BuiltinProtectedError):
            asyncio.run(handler({"id": "personal"}))

    def test_update_custom_merges_settings(
        self,
        db: DatabaseManager,
    ) -> None:
        """A partial settings dict on update merges with stored settings."""
        create = make_create_security_profile_handler(db)
        created = asyncio.run(
            create(
                {
                    "name": "merge-test",
                    "base_profile_id": "developer",
                    "settings_overrides": {
                        "agent_limits.iterations.max": 100,
                    },
                },
            ),
        )
        custom_id = created["profile"]["id"]

        update = make_update_security_profile_handler(db)
        updated = asyncio.run(
            update(
                {
                    "id": custom_id,
                    "settings": {"agent_limits.tools.file_write_per_loop": 99},
                },
            ),
        )
        assert updated["profile"]["settings"]["agent_limits.iterations.max"] == 100
        assert (
            updated["profile"]["settings"]["agent_limits.tools.file_write_per_loop"]
            == 99
        )

    def test_set_active_changes_pointer(
        self,
        db: DatabaseManager,
    ) -> None:
        """set_active updates app_settings.active_profile_id."""
        handler = make_set_active_security_profile_handler(db)
        result = asyncio.run(handler({"id": "developer"}))
        assert result["active_profile_id"] == "developer"
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'active_profile_id'"
        ).fetchone()
        assert row[0] == "developer"

    def test_set_active_unknown_raises(
        self,
        db: DatabaseManager,
    ) -> None:
        """Activating an unknown profile id raises ProfileNotFoundError."""
        handler = make_set_active_security_profile_handler(db)
        with pytest.raises(ProfileNotFoundError):
            asyncio.run(handler({"id": "ghost"}))

    def test_delete_active_falls_back_to_personal(
        self,
        db: DatabaseManager,
    ) -> None:
        """Deleting the active custom profile resets active to personal."""
        create = make_create_security_profile_handler(db)
        custom = asyncio.run(create({"name": "victim"}))
        custom_id = custom["profile"]["id"]

        activate = make_set_active_security_profile_handler(db)
        asyncio.run(activate({"id": custom_id}))

        delete = make_delete_security_profile_handler(db)
        result = asyncio.run(delete({"id": custom_id}))

        assert result["deleted_id"] == custom_id
        assert result["new_active_id"] == "personal"
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'active_profile_id'"
        ).fetchone()
        assert row[0] == "personal"

    def test_settings_json_round_trip(
        self,
        db: DatabaseManager,
    ) -> None:
        """The settings dict survives JSON serialisation in storage."""
        create = make_create_security_profile_handler(db)
        custom = asyncio.run(create({"name": "round-trip"}))
        # The stored row keeps the dict as JSON; decoding it must
        # match what the handler reported.
        row = db.execute(
            "SELECT settings_json FROM security_profiles WHERE id = ?",
            (custom["profile"]["id"],),
        ).fetchone()
        assert json.loads(row[0]) == custom["profile"]["settings"]
