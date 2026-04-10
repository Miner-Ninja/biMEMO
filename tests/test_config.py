"""Tests for application configuration loading and validation.

Covers successful configuration from environment variables, missing
required variable detection, and settings singleton behavior.
"""

import pytest
from backend.config import Settings, get_settings, reset_settings
from pydantic import ValidationError


class TestSettings:
    """Tests for the Settings Pydantic model."""

    def test_settings_loads_from_env(self):
        """Verify settings are loaded from environment variables."""
        settings = Settings()  # type: ignore[call-arg]
        assert settings.debug is True
        assert settings.key_source == "file"
        assert settings.key_file_path == "/tmp/test-master.key"
        assert settings.database_path == "/tmp/test-zeroclaw.db"
        assert settings.compute_config_path == "/tmp/test-compute.json"
        assert settings.searxng_url == "http://localhost:8888/search"
        assert settings.host == "127.0.0.1"
        assert settings.port == 8000

    def test_settings_missing_required_raises(self, monkeypatch):
        """Verify missing required env var causes ValidationError."""
        monkeypatch.delenv("BIMEMO_DEBUG")
        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_settings_invalid_port_raises(self, monkeypatch):
        """Verify non-integer port causes ValidationError."""
        monkeypatch.setenv("BIMEMO_PORT", "not-a-number")
        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]


class TestKeySourceValidation:
    """Tests for key source configuration validation."""

    def test_file_key_source_valid(self):
        """Verify file key source loads with key_file_path."""
        settings = Settings()  # type: ignore[call-arg]
        assert settings.key_source == "file"
        assert settings.key_file_path == "/tmp/test-master.key"

    def test_invalid_key_source_raises(self, monkeypatch):
        """Verify invalid key_source raises."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "invalid")
        with pytest.raises(ValidationError, match="Invalid key_source"):
            Settings()  # type: ignore[call-arg]

    def test_file_key_source_missing_path_raises(self, monkeypatch):
        """Verify missing key_file_path raises for file source."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "file")
        monkeypatch.setenv("BIMEMO_KEY_FILE_PATH", "")
        with pytest.raises(ValidationError, match="key_file_path"):
            Settings()  # type: ignore[call-arg]


class TestGetSettings:
    """Tests for the get_settings singleton function."""

    def test_get_settings_returns_instance(self):
        """Verify get_settings returns a Settings object."""
        reset_settings()
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_returns_same_instance(self):
        """Verify get_settings returns the same singleton."""
        reset_settings()
        first = get_settings()
        second = get_settings()
        assert first is second

    def test_reset_settings_clears_singleton(self):
        """Verify reset_settings forces a new instance on next call."""
        reset_settings()
        first = get_settings()
        reset_settings()
        second = get_settings()
        assert first is not second
