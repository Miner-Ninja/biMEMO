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
        assert settings.key_source == "password"
        assert settings.key_file_path == ""
        assert settings.encrypted_key_file_path == "/tmp/test-master.key.enc"
        assert settings.automatic_unlock is True
        assert settings.unlock_password_file is None
        assert settings.unlock_password == "test-startup-password"
        assert settings.database_path == "/tmp/test-zeroclaw.db"
        assert settings.searxng_url == "http://localhost:8888/search"
        assert settings.host == "127.0.0.1"
        assert settings.port == 8000
        assert settings.llm_response_format == "text"

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

    def test_file_key_source_valid(self, monkeypatch):
        """Verify file key source loads with key_file_path."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "file")
        monkeypatch.setenv("BIMEMO_KEY_FILE_PATH", "/tmp/test-master.key")
        settings = Settings()  # type: ignore[call-arg]
        assert settings.key_source == "file"
        assert settings.key_file_path == "/tmp/test-master.key"

    def test_password_key_source_valid(self, monkeypatch):
        """Verify password key source loads with encrypted key path."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            "/tmp/test-master.key.enc",
        )
        monkeypatch.setenv("BIMEMO_AUTOMATIC_UNLOCK", "true")
        monkeypatch.setenv(
            "BIMEMO_UNLOCK_PASSWORD_FILE",
            "/run/secrets/bimemo_unlock_password",
        )
        monkeypatch.setenv("BIMEMO_UNLOCK_PASSWORD", "dev-password")

        settings = Settings()  # type: ignore[call-arg]

        assert settings.key_source == "password"
        assert settings.encrypted_key_file_path == "/tmp/test-master.key.enc"
        assert settings.automatic_unlock is True
        assert settings.unlock_password_file == "/run/secrets/bimemo_unlock_password"
        assert settings.unlock_password == "dev-password"

    def test_password_key_source_manual_unlock_valid(self, monkeypatch):
        """Verify password key source supports manual unlock without a source."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            "/tmp/test-master.key.enc",
        )
        monkeypatch.setenv("BIMEMO_AUTOMATIC_UNLOCK", "false")
        monkeypatch.delenv("BIMEMO_UNLOCK_PASSWORD", raising=False)
        monkeypatch.delenv("BIMEMO_UNLOCK_PASSWORD_FILE", raising=False)

        settings = Settings()  # type: ignore[call-arg]

        assert settings.key_source == "password"
        assert settings.automatic_unlock is False
        assert settings.unlock_password_file is None
        assert settings.unlock_password is None

    def test_automatic_unlock_missing_source_raises(self, monkeypatch):
        """Verify automatic unlock requires an explicit password source."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            "/tmp/test-master.key.enc",
        )
        monkeypatch.setenv("BIMEMO_AUTOMATIC_UNLOCK", "true")
        monkeypatch.delenv("BIMEMO_UNLOCK_PASSWORD", raising=False)
        monkeypatch.delenv("BIMEMO_UNLOCK_PASSWORD_FILE", raising=False)

        with pytest.raises(ValidationError, match="automatic_unlock"):
            Settings()  # type: ignore[call-arg]

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

    def test_password_key_source_missing_path_raises(self, monkeypatch):
        """Verify missing encrypted key path raises for password source."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv("BIMEMO_ENCRYPTED_KEY_FILE_PATH", "")
        with pytest.raises(ValidationError, match="encrypted_key_file_path"):
            Settings()  # type: ignore[call-arg]


class TestLlmResponseFormatValidation:
    """Tests for the BIMEMO_LLM_RESPONSE_FORMAT setting validation."""

    def test_text_value_accepted(self, monkeypatch):
        """Verify text is a valid response format."""
        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "text")
        settings = Settings()  # type: ignore[call-arg]
        assert settings.llm_response_format == "text"

    def test_json_schema_value_accepted(self, monkeypatch):
        """Verify json_schema is a valid response format."""
        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "json_schema")
        settings = Settings()  # type: ignore[call-arg]
        assert settings.llm_response_format == "json_schema"

    def test_invalid_value_raises(self, monkeypatch):
        """Verify an unknown value raises with the accepted choices."""
        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "auto")
        with pytest.raises(ValidationError, match="llm_response_format"):
            Settings()  # type: ignore[call-arg]

    def test_missing_value_raises(self, monkeypatch):
        """Verify missing env var raises ValidationError."""
        monkeypatch.delenv("BIMEMO_LLM_RESPONSE_FORMAT")
        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]


class TestLlmResponseStreamValidation:
    """Tests for the BIMEMO_LLM_RESPONSE_STREAM setting validation."""

    def test_true_value_accepted(self, monkeypatch):
        """Verify true is a valid stream flag."""
        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_STREAM", "true")
        settings = Settings()  # type: ignore[call-arg]
        assert settings.llm_response_stream is True

    def test_false_value_accepted(self, monkeypatch):
        """Verify false is a valid stream flag."""
        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_STREAM", "false")
        settings = Settings()  # type: ignore[call-arg]
        assert settings.llm_response_stream is False

    def test_invalid_value_raises(self, monkeypatch):
        """Verify an unparseable boolean raises ValidationError."""
        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_STREAM", "maybe")
        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_missing_value_raises(self, monkeypatch):
        """Verify missing env var raises ValidationError."""
        monkeypatch.delenv("BIMEMO_LLM_RESPONSE_STREAM")
        with pytest.raises(ValidationError):
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
