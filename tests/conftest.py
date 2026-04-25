"""Shared test fixtures for biMEMO test suite.

Provides common fixtures including test settings, environment
variable management, and a personal-profile ProfileResolver
stub that defense modules read from when tests do not wire up
their own resolver.
"""

import pytest

from backend.security.profiles.builtins import PERSONAL_SETTINGS
from backend.security.profiles.models import SecurityProfile
from backend.security.profiles.resolver import ProfileResolver, set_resolver


class _StubProfileStorage:
    """Minimal storage stub that always returns the personal profile."""

    async def get_active(self) -> tuple[str, SecurityProfile]:
        """Return the canonical personal profile id and settings."""
        return (
            "personal",
            SecurityProfile(
                id="personal",
                name="personal",
                description="",
                is_builtin=True,
                settings=dict(PERSONAL_SETTINGS),
                created_at="",
                updated_at="",
            ),
        )


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    """Set required environment variables for all tests."""
    cleared_env_vars = (
        "BIMEMO_KEY_FILE_PATH",
        "BIMEMO_UNLOCK_PASSWORD_FILE",
    )
    for key in cleared_env_vars:
        monkeypatch.delenv(key, raising=False)

    env_vars = {
        "BIMEMO_DEBUG": "true",
        "BIMEMO_KEY_SOURCE": "password",
        "BIMEMO_ENCRYPTED_KEY_FILE_PATH": "/tmp/test-master.key.enc",
        "BIMEMO_AUTOMATIC_UNLOCK": "true",
        "BIMEMO_UNLOCK_PASSWORD": "test-startup-password",
        "BIMEMO_DATABASE_PATH": "/tmp/test-zeroclaw.db",
        "BIMEMO_SEARXNG_URL": "http://localhost:8888/search",
        "BIMEMO_HOST": "127.0.0.1",
        "BIMEMO_PORT": "8000",
        "BIMEMO_LLM_RESPONSE_FORMAT": "text",
        "BIMEMO_LLM_RESPONSE_STREAM": "false",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _personal_profile_resolver():
    """Wire a personal-profile resolver into every test by default.

    Defense modules (content_filter, validation, rate_limits, agent
    loop, trust zones, ...) read tunables via
    ``ProfileResolver.get_resolver()``. Without this fixture every
    test would have to construct one. Tests that need a different
    profile call ``set_resolver(custom_resolver)`` explicitly.
    """
    import asyncio

    from backend.security.content_filter import reset_filter_cache

    resolver = ProfileResolver(_StubProfileStorage())
    # Run load() in a fresh loop so we do not disturb any
    # pytest-asyncio event loop the test itself may set up.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(resolver.load())
    finally:
        loop.close()
    set_resolver(resolver)
    reset_filter_cache()
    yield
    set_resolver(None)
    reset_filter_cache()
