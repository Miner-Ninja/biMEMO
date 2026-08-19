"""Tests for API request Pydantic models.

Covers valid inputs, validation errors, and extra
field rejection for all request models.
"""

import pytest
from pydantic import ValidationError

from backend.models.requests import (
    ChatRequest,
    ConfirmRequest,
    IntegrationCreateRequest,
    PasswordAuthRequest,
    ReauthRequest,
    UserProfileRequest,
)


class TestChatRequest:
    """Tests for ChatRequest model."""

    def test_valid(self):
        """Verify valid chat request."""
        req = ChatRequest(message="Hello")
        assert req.message == "Hello"

    def test_empty_rejected(self):
        """Verify empty message rejected."""
        with pytest.raises(ValidationError):
            ChatRequest(message="")

    def test_too_long_rejected(self):
        """Verify message over 100K chars rejected."""
        with pytest.raises(ValidationError):
            ChatRequest(message="x" * 100_001)

    def test_max_length_accepted(self):
        """Verify message at exactly 100K chars accepted."""
        req = ChatRequest(message="x" * 100_000)
        assert len(req.message) == 100_000

    def test_file_ids_are_normalized_and_deduplicated(self):
        """Verify file_ids must be UUIDs and duplicates collapse."""
        raw_id = "550E8400-E29B-41D4-A716-446655440000"
        req = ChatRequest(message="Hello", file_ids=[raw_id, raw_id.lower()])
        assert req.file_ids == ["550e8400-e29b-41d4-a716-446655440000"]

    def test_invalid_file_id_rejected(self):
        """Verify malformed file IDs are rejected."""
        with pytest.raises(ValidationError):
            ChatRequest(message="Hello", file_ids=["not-a-uuid"])


class TestConfirmRequest:
    """Tests for ConfirmRequest model."""

    def test_approve(self):
        """Verify approve decision accepted."""
        req = ConfirmRequest(action_id="abc123", decision="approve")
        assert req.decision == "approve"

    def test_deny(self):
        """Verify deny decision accepted."""
        req = ConfirmRequest(action_id="abc123", decision="deny")
        assert req.decision == "deny"

    def test_invalid_decision_rejected(self):
        """Verify invalid decision rejected."""
        with pytest.raises(ValidationError):
            ConfirmRequest(action_id="abc123", decision="maybe")

    def test_empty_action_id_rejected(self):
        """Verify empty action_id rejected."""
        with pytest.raises(ValidationError):
            ConfirmRequest(action_id="", decision="approve")


class TestIntegrationCreateRequest:
    """Tests for IntegrationCreateRequest model."""

    def test_valid(self):
        """Verify valid integration request."""
        req = IntegrationCreateRequest(
            name="email-work",
            type="email",
            config={"host": "smtp.example.com"},
        )
        assert req.name == "email-work"
        assert req.type == "email"

    def test_empty_config_default(self):
        """Verify empty config defaults to empty dict."""
        req = IntegrationCreateRequest(name="test", type="api")
        assert req.config == {}

    def test_nested_config_values_accepted(self):
        """Verify integration config accepts ordinary JSON values."""
        req = IntegrationCreateRequest(
            name="telegram-prod",
            type="telegram",
            config={
                "bot_token": "secret",
                "enabled": True,
                "retries": 3,
                "scopes": ["alerts", "ops"],
                "proxy": {"host": "127.0.0.1", "port": 1080},
            },
        )
        assert req.config["enabled"] is True
        assert req.config["retries"] == 3

    def test_name_too_long_rejected(self):
        """Verify name over 100 chars rejected."""
        with pytest.raises(ValidationError):
            IntegrationCreateRequest(name="x" * 101, type="api")

    def test_type_too_long_rejected(self):
        """Verify type over 50 chars rejected."""
        with pytest.raises(ValidationError):
            IntegrationCreateRequest(name="test", type="x" * 51)


class TestReauthRequest:
    """Tests for ReauthRequest model."""

    def test_valid(self):
        """Verify valid reauth request."""
        req = ReauthRequest(password="mypass")
        assert req.password == "mypass"

    def test_empty_rejected(self):
        """Verify empty password rejected."""
        with pytest.raises(ValidationError):
            ReauthRequest(password="")


class TestPasswordAuthRequest:
    """Tests for PasswordAuthRequest model."""

    def test_valid(self):
        """Verify valid password request."""
        req = PasswordAuthRequest(password="strongpass")
        assert req.password == "strongpass"

    def test_short_password_rejected(self):
        """Verify passwords shorter than 8 chars are rejected."""
        with pytest.raises(ValidationError):
            PasswordAuthRequest(password="short")

    def test_extra_fields_rejected(self):
        """Verify extra fields rejected."""
        with pytest.raises(ValidationError):
            PasswordAuthRequest(password="strongpass", extra="bad")


class TestUserProfileRequest:
    """Tests for UserProfileRequest model."""

    def test_valid(self):
        """Verify valid user profile payload."""
        req = UserProfileRequest(
            display_name="Ada",
            timezone="Europe/Vilnius",
            language="lt",
            custom_instructions="Be concise",
        )
        assert req.display_name == "Ada"

    def test_extra_fields_rejected(self):
        """Verify extra fields rejected."""
        with pytest.raises(ValidationError):
            UserProfileRequest(display_name="Ada", extra="bad")
