"""Startup state detection for password-first application boot."""

from enum import StrEnum
from pathlib import Path


class StartupState(StrEnum):
    """Visible startup state before the full multi-process app is ready.

    Attributes:
        SETUP: No encrypted key file exists and no startup password is configured.
        LOCKED: Encrypted key file exists but no startup password is configured.
        AUTO_UNLOCK: A startup password source is configured.
        AUTO_UNLOCK_FAILED: Configured automatic unlock failed and needs manual unlock.
        READY: Legacy non-password startup can continue directly to normal services.
    """

    SETUP = "setup"
    LOCKED = "locked"
    AUTO_UNLOCK = "auto_unlock"
    AUTO_UNLOCK_FAILED = "auto_unlock_failed"
    READY = "ready"


def _has_startup_password_source(settings: object) -> bool:
    """Return whether settings enable and configure automatic unlock."""
    if not getattr(settings, "automatic_unlock", False):
        return False
    return bool(
        settings.unlock_password_file is not None
        or settings.unlock_password is not None
    )


def _has_encrypted_key_file(settings: object) -> bool:
    """Return whether the configured encrypted master-key file exists."""
    return Path(settings.encrypted_key_file_path).is_file()


def determine_startup_state(settings: object) -> StartupState:
    """Determine the startup state from key mode, key file, and password source.

    Args:
        settings: Application settings object with key-source fields.

    Returns:
        Startup state for the current process boot.
    """
    if settings.key_source != "password":
        return StartupState.READY
    if _has_startup_password_source(settings):
        return StartupState.AUTO_UNLOCK
    if _has_encrypted_key_file(settings):
        return StartupState.LOCKED
    return StartupState.SETUP
