"""Unlock flow for decrypting the master key with password.

Provides the POST /unlock endpoint logic including Argon2id key
derivation, AES-256-GCM decryption, brute-force rate limiting with
exponential backoff, and attempt logging to stdout.
"""

import logging
import time

from backend.security.encrypted_key_file import (
    WrongPasswordError,
    decrypt_master_key,
    read_key_file,
)


logger = logging.getLogger(__name__)

# --- Rate limiting ---
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [30, 60, 120, 300]


class UnlockRateLimitError(Exception):
    """Raised when unlock attempts are rate-limited."""

    def __init__(self, retry_after: float) -> None:
        """Initialize with retry delay.

        Args:
            retry_after: Seconds until next attempt is allowed.
        """
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after:.0f}s")


class UnlockManager:
    """Manages the unlock flow with rate limiting.

    Attributes:
        key_file_path: Path to the encrypted key file.
        failed_attempts: Number of consecutive failed attempts.
        last_attempt_time: Timestamp of the last failed attempt.
    """

    def __init__(self, key_file_path: str) -> None:
        """Initialize unlock manager.

        Args:
            key_file_path: Path to the encrypted key file.
        """
        self.key_file_path = key_file_path
        self.failed_attempts = 0
        self.last_attempt_time = 0.0

    def _record_failed_attempt(self) -> None:
        """Update the internal state after one failed unlock attempt."""
        self.failed_attempts += 1
        self.last_attempt_time = time.monotonic()

    def _reset_failed_attempts(self) -> None:
        """Reset the internal failed-attempt counters after a successful unlock."""
        self.failed_attempts = 0
        self.last_attempt_time = 0.0

    def _log_failed_attempt(self) -> None:
        """Log one failed unlock attempt."""
        logger.warning(
            "Unlock failed (attempt %d/%d)",
            self.failed_attempts,
            MAX_ATTEMPTS,
        )

    def _handle_failed_attempt(self, error: Exception) -> None:
        """Record and re-raise one failed unlock attempt."""
        self._record_failed_attempt()
        self._log_failed_attempt()
        if isinstance(error, WrongPasswordError):
            raise error
        raise WrongPasswordError("Invalid password") from error

    def _backoff_seconds(self) -> float:
        """Return the current backoff window for the failure count."""
        backoff_index = min(
            self.failed_attempts - MAX_ATTEMPTS, len(BACKOFF_SECONDS) - 1
        )
        return BACKOFF_SECONDS[backoff_index]

    def attempt_unlock(self, password: str) -> bytes:
        """Attempt to unlock the system with the given password.

        Args:
            password: User's unlock password.

        Returns:
            Decrypted 32-byte master key.

        Raises:
            UnlockRateLimitError: If rate limit is in effect.
            WrongPasswordError: If the password is incorrect.
        """
        self._check_rate_limit()

        encrypted_blob = read_key_file(self.key_file_path)

        try:
            master_key = decrypt_master_key(encrypted_blob, password)
            self._reset_failed_attempts()
            logger.info("Unlock successful")
            return master_key
        except (WrongPasswordError, Exception) as e:
            self._handle_failed_attempt(e)

    def _check_rate_limit(self) -> None:
        """Check if rate limiting is in effect.

        Raises:
            UnlockRateLimitError: If rate limit is active.
        """
        if self.failed_attempts < MAX_ATTEMPTS:
            return

        backoff = self._backoff_seconds()
        elapsed = time.monotonic() - self.last_attempt_time

        if elapsed < backoff:
            retry_after = backoff - elapsed
            raise UnlockRateLimitError(retry_after)
