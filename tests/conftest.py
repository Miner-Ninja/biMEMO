"""Shared test fixtures for biMEMO test suite.

Provides common fixtures including test settings and
environment variable management for isolated test execution.
"""

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
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
