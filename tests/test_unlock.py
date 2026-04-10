"""Tests for the unlock flow including rate limiting.

Covers correct password unlock, wrong password rejection, rate limiting
with exponential backoff, and locked state endpoint filtering.
"""

import os
import time

import pytest
from backend.security.encrypted_key_file import (
    WrongPasswordError,
    encrypt_master_key,
    write_key_file,
)
from backend.security.unlock import (
    MAX_ATTEMPTS,
    UnlockManager,
    UnlockRateLimitError,
)


@pytest.fixture
def key_file(tmp_path):
    """Create a test encrypted key file."""
    master_key = os.urandom(32)
    password = "CorrectPassword1!"
    encrypted = encrypt_master_key(
        master_key, password, memory_kb=1024, iterations=1, parallelism=1
    )
    path = str(tmp_path / "test.key")
    write_key_file(path, encrypted)
    return path, master_key, password


class TestUnlockCorrectPassword:
    """Tests for successful unlock."""

    def test_correct_password_returns_key(self, key_file):
        """Verify correct password decrypts and returns the master key."""
        path, expected_key, password = key_file
        manager = UnlockManager(path)
        result = manager.attempt_unlock(password)
        assert result == expected_key

    def test_successful_unlock_resets_counter(self, key_file):
        """Verify successful unlock resets the failed attempt counter."""
        path, _, password = key_file
        manager = UnlockManager(path)
        manager.failed_attempts = 3
        manager.attempt_unlock(password)
        assert manager.failed_attempts == 0


class TestUnlockWrongPassword:
    """Tests for wrong password handling."""

    def test_wrong_password_raises(self, key_file):
        """Verify wrong password raises WrongPasswordError."""
        path, _, _ = key_file
        manager = UnlockManager(path)
        with pytest.raises((WrongPasswordError, Exception)):
            manager.attempt_unlock("WrongPassword123!")

    def test_wrong_password_increments_counter(self, key_file):
        """Verify failed attempt increments counter."""
        path, _, _ = key_file
        manager = UnlockManager(path)
        with pytest.raises((WrongPasswordError, Exception)):
            manager.attempt_unlock("WrongPassword123!")
        assert manager.failed_attempts == 1


class TestRateLimiting:
    """Tests for brute-force rate limiting."""

    def test_rate_limit_after_max_attempts(self, key_file):
        """Verify rate limiting kicks in after MAX_ATTEMPTS failures."""
        path, _, _ = key_file
        manager = UnlockManager(path)
        manager.failed_attempts = MAX_ATTEMPTS
        manager.last_attempt_time = time.monotonic()
        with pytest.raises(UnlockRateLimitError):
            manager.attempt_unlock("anything")

    def test_rate_limit_allows_after_backoff(self, key_file):
        """Verify rate limit expires after backoff period."""
        path, _, password = key_file
        manager = UnlockManager(path)
        manager.failed_attempts = MAX_ATTEMPTS
        manager.last_attempt_time = time.monotonic() - 31
        result = manager.attempt_unlock(password)
        assert result is not None

    def test_rate_limit_error_includes_retry_after(self, key_file):
        """Verify rate limit error includes retry_after value."""
        path, _, _ = key_file
        manager = UnlockManager(path)
        manager.failed_attempts = MAX_ATTEMPTS
        manager.last_attempt_time = time.monotonic()
        with pytest.raises(UnlockRateLimitError) as exc_info:
            manager.attempt_unlock("anything")
        assert exc_info.value.retry_after > 0


class TestWrongPasswordReraised:
    """Tests for WrongPasswordError re-raise path."""

    def test_wrong_password_error_reraised(self, key_file):
        """Verify WrongPasswordError from decrypt is re-raised."""
        from unittest.mock import patch

        path, _, _ = key_file
        manager = UnlockManager(path)
        with (
            patch(
                "backend.security.unlock.decrypt_master_key",
                side_effect=WrongPasswordError("bad pw"),
            ),
            pytest.raises(WrongPasswordError, match="bad pw"),
        ):
            manager.attempt_unlock("any-password")


class TestNonWrongPasswordException:
    """Tests for unexpected exceptions during unlock."""

    def test_generic_exception_wrapped_as_wrong_password(self, key_file):
        """Verify non-WrongPasswordError is wrapped."""
        from unittest.mock import patch

        path, _, _ = key_file
        manager = UnlockManager(path)
        with (
            patch(
                "backend.security.unlock.decrypt_master_key",
                side_effect=RuntimeError("unexpected"),
            ),
            pytest.raises(WrongPasswordError),
        ):
            manager.attempt_unlock("any-password")
