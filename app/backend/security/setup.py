"""First-run setup flow for master key generation and password enrollment.

Handles initial system setup when no encrypted key file exists: generates
a 256-bit master key, prints a one-time setup token to stdout, and
provides a setup endpoint that accepts the token and user password to
encrypt and store the master key. The setup endpoint is permanently
disabled after successful completion.
"""

import logging
import os
import secrets

from backend.security.encrypted_key_file import encrypt_master_key, write_key_file
from backend.security.master_key import MASTER_KEY_LENGTH


logger = logging.getLogger(__name__)

# --- Password validation ---
MIN_PASSWORD_LENGTH = 16
MIN_PASSPHRASE_LENGTH = 24


class SetupError(Exception):
    """Raised when setup operations fail."""


class SetupManager:
    """Manages the first-run setup flow.

    Attributes:
        key_file_path: Path to the encrypted key file.
        setup_token: One-time token required to access setup.
        setup_complete: Whether setup has been completed.
    """

    def __init__(self, key_file_path: str) -> None:
        """Initialize setup manager.

        Args:
            key_file_path: Path where the encrypted key file will be stored.
        """
        self.key_file_path = key_file_path
        self.setup_token: str | None = None
        self.setup_complete = False

    @property
    def needs_setup(self) -> bool:
        """Check if first-run setup is needed.

        Returns:
            True if no key file exists and setup hasn't been completed.
        """
        return not os.path.exists(self.key_file_path) and not self.setup_complete

    def generate_setup_token(self) -> str:
        """Generate and store a one-time setup token.

        The token is printed to stdout (Docker logs) for the user to copy.

        Returns:
            The generated setup token.
        """
        self.setup_token = secrets.token_urlsafe(32)
        logger.info(
            "SETUP TOKEN (copy this, it will not be shown again): %s",
            self.setup_token,
        )
        return self.setup_token

    def _finalize_setup(self) -> None:
        """Mark setup as complete and clear the one-time setup token."""
        self.setup_complete = True
        self.setup_token = None
        logger.info("First-run setup completed, key file written")

    def validate_token(self, token: str) -> bool:
        """Validate the provided setup token.

        Args:
            token: Token to validate against the stored token.

        Returns:
            True if the token matches.
        """
        if self.setup_token is None:
            return False
        return secrets.compare_digest(token, self.setup_token)

    def complete_setup(self, token: str, password: str) -> bytes:
        """Complete first-run setup with token validation and password enrollment.

        Generates a master key, encrypts it with the provided password,
        and writes the encrypted key file. Disables setup permanently.

        Args:
            token: One-time setup token for authorization.
            password: User's chosen unlock password.

        Returns:
            The generated master key bytes.

        Raises:
            SetupError: If token is invalid, password is weak, or setup
                was already completed.
        """
        if self.setup_complete:
            raise SetupError("Setup already completed")

        if not self.validate_token(token):
            raise SetupError("Invalid setup token")

        _validate_password_strength(password)

        master_key = secrets.token_bytes(MASTER_KEY_LENGTH)
        encrypted_blob = encrypt_master_key(master_key, password)
        write_key_file(self.key_file_path, encrypted_blob)
        self._finalize_setup()

        return master_key


def _password_missing_requirements(password: str) -> list[str]:
    """Return the missing complexity requirements for one short password."""
    missing: list[str] = []
    if not any(c.isupper() for c in password):
        missing.append("uppercase letter")
    if not any(c.isdigit() for c in password):
        missing.append("digit")
    if not any(not c.isalnum() for c in password):
        missing.append("special character")
    return missing


def _minimum_password_length_text() -> str:
    """Return the standard password-length requirement text."""
    return (
        f"Password must be at least {MIN_PASSWORD_LENGTH} characters "
        f"(or {MIN_PASSPHRASE_LENGTH}+ for passphrase mode)"
    )


def _validate_password_strength(password: str) -> None:
    """Validate password meets strength requirements.

    Accepts either:
    - 16+ chars with uppercase, digit, and special character
    - 24+ chars passphrase (no complexity requirements)

    Args:
        password: Password to validate.

    Raises:
        SetupError: If password does not meet requirements.
    """
    if len(password) >= MIN_PASSPHRASE_LENGTH:
        return

    if len(password) < MIN_PASSWORD_LENGTH:
        raise SetupError(_minimum_password_length_text())
    missing = _password_missing_requirements(password)

    if missing:
        raise SetupError(f"Password missing: {', '.join(missing)}")
