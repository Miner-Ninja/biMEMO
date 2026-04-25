"""Tests for ProfileResolver lookup, caching, and reload.

The resolver feeds every defense module on the FastAPI process,
so its three contracts matter:

- A locked catalog entry always returns its safe default value
  regardless of what the active profile stores.
- An editable key missing from the active profile (e.g., the
  catalog grew newer keys than the stored profile) falls back to
  ``defaults_personal``.
- ``reload()`` swaps the cache atomically, so a get() right
  after activation returns the new profile's value.
"""

import pytest
from backend.security.profiles.builtins import (
    DEVELOPER_SETTINGS,
    PERSONAL_SETTINGS,
)
from backend.security.profiles.models import SecurityProfile
from backend.security.profiles.resolver import (
    ProfileResolver,
    get_resolver,
    set_resolver,
)


class _StubStorage:
    """Minimal storage stub that returns a fixed profile."""

    def __init__(self, profile_id: str, settings: dict) -> None:
        """Store the profile id and a copy of the settings dict."""
        self._profile_id = profile_id
        self._settings = dict(settings)

    def update(self, profile_id: str, settings: dict) -> None:
        """Replace the active profile that get_active will return."""
        self._profile_id = profile_id
        self._settings = dict(settings)

    async def get_active(self) -> tuple[str, SecurityProfile]:
        """Return the canonical active profile id and SecurityProfile."""
        return (
            self._profile_id,
            SecurityProfile(
                id=self._profile_id,
                name=self._profile_id,
                description="",
                is_builtin=False,
                settings=dict(self._settings),
                created_at="",
                updated_at="",
            ),
        )


class TestProfileResolver:
    """Exercise load/get/reload on a stubbed storage."""

    @pytest.fixture
    def storage(self) -> _StubStorage:
        """Return a stub storage starting on a personal-shaped profile."""
        return _StubStorage("personal", PERSONAL_SETTINGS)

    @pytest.fixture
    async def resolver(self, storage: _StubStorage) -> ProfileResolver:
        """Return a ProfileResolver loaded against the stub storage."""
        instance = ProfileResolver(storage)
        await instance.load()
        return instance

    async def test_load_populates_cache(
        self,
        resolver: ProfileResolver,
    ) -> None:
        """After load() the resolver reports loaded and active id."""
        assert resolver.is_loaded
        assert resolver.active_profile_id == "personal"

    async def test_get_returns_profile_value(
        self,
        resolver: ProfileResolver,
    ) -> None:
        """Editable keys present in the profile return the stored value."""
        assert resolver.get_int("agent_limits.iterations.max") == 20

    async def test_get_locked_key_returns_safe_default(
        self,
        resolver: ProfileResolver,
    ) -> None:
        """Locked catalog entries always return the catalog default."""
        assert resolver.get_bool("network.block_cloud_metadata") is True

    async def test_get_unknown_key_raises(
        self,
        resolver: ProfileResolver,
    ) -> None:
        """Unknown keys raise KeyError when no fallback is supplied."""
        with pytest.raises(KeyError):
            resolver.get("does.not.exist")

    async def test_get_unknown_key_with_fallback(
        self,
        resolver: ProfileResolver,
    ) -> None:
        """Unknown keys return the supplied fallback when one is given."""
        assert resolver.get("does.not.exist", "fallback") == "fallback"

    async def test_reload_swaps_cache(
        self,
        resolver: ProfileResolver,
        storage: _StubStorage,
    ) -> None:
        """reload() picks up a new active profile from storage."""
        storage.update("developer", DEVELOPER_SETTINGS)
        await resolver.reload()
        assert resolver.active_profile_id == "developer"
        assert resolver.get_int("agent_limits.iterations.max") == 80

    async def test_catalog_fallback_for_missing_key(
        self,
        resolver: ProfileResolver,
        storage: _StubStorage,
    ) -> None:
        """A key missing from the stored profile falls back to defaults_personal."""
        stale = dict(DEVELOPER_SETTINGS)
        del stale["agent_limits.iterations.max"]
        storage.update("stale", stale)
        await resolver.reload()
        # Catalog default for personal is 20, even though developer would be 80.
        assert resolver.get_int("agent_limits.iterations.max") == 20

    async def test_typed_accessors_enforce_shape(
        self,
        resolver: ProfileResolver,
    ) -> None:
        """get_bool / get_int / get_list_str raise on shape mismatch."""
        with pytest.raises(TypeError):
            resolver.get_int("input_filter.block_code_execution")
        with pytest.raises(TypeError):
            resolver.get_bool("agent_limits.iterations.max")


class TestModuleSingleton:
    """Cover get_resolver / set_resolver process-wide registration."""

    def test_get_without_set_raises(self) -> None:
        """Calling get_resolver before set_resolver raises a RuntimeError."""
        # The autouse personal-profile resolver fixture wires a resolver
        # for every test. Explicitly clear it for this assertion.
        set_resolver(None)
        with pytest.raises(RuntimeError):
            get_resolver()

    async def test_set_then_get(self) -> None:
        """set_resolver registers the instance for non-DI lookups."""
        instance = ProfileResolver(_StubStorage("personal", PERSONAL_SETTINGS))
        await instance.load()
        set_resolver(instance)
        try:
            assert get_resolver() is instance
        finally:
            set_resolver(None)
