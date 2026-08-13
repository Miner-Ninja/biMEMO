"""Shared test fixtures for biMEMO test suite."""

import pytest


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
        "BIMEMO_STRUCTURED_FINALIZATION_ENABLED": "false",
        "BIMEMO_LLM_NATIVE_TOOLS_SUPPORT": "unknown",
        "BIMEMO_LLM_EXACT_NAMED_CHOICE_SUPPORT": "unknown",
        "BIMEMO_LLM_REQUIRED_NATIVE_DECISION_SUPPORT": "unknown",
        "BIMEMO_LLM_CUSTOM_SCHEMA_SUPPORT": "unknown",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _reset_policy_backed_caches():
    """Reset caches derived from the static assistant policy."""
    from backend.security.content_filter import reset_filter_cache

    reset_filter_cache()
    yield
    reset_filter_cache()
