"""Startup password loading for password-based application unlock."""

from dataclasses import dataclass
from pathlib import Path


class StartupPasswordError(Exception):
    """Raised when startup password loading fails."""


@dataclass(frozen=True)
class StartupPasswordSource:
    """Resolved startup password source.

    Attributes:
        password: The resolved startup password.
        source: Non-sensitive source label used for diagnostics.
    """

    password: str
    source: str


# --- Startup source labels ---
STARTUP_FILE_SOURCE = "startup-file"
STARTUP_ENV_SOURCE = "startup-env"


def _strip_one_trailing_newline(raw_value: str) -> str:
    """Remove one trailing newline sequence from a secret-file value."""
    if raw_value.endswith("\r\n"):
        return raw_value[:-2]
    if raw_value.endswith(("\n", "\r")):
        return raw_value[:-1]
    return raw_value


def _read_password_file(path: str) -> str:
    """Read and normalize one startup password file."""
    try:
        raw_value = Path(path).read_text()
    except OSError as e:
        raise StartupPasswordError("Cannot read startup password file") from e
    return _strip_one_trailing_newline(raw_value)


def _required_password(raw_value: str, source: str) -> StartupPasswordSource:
    """Return a non-empty startup password source or raise."""
    if raw_value == "":
        raise StartupPasswordError(f"Startup password from {source} is empty")
    return StartupPasswordSource(password=raw_value, source=source)


def load_startup_password(
    *,
    password_file: str | None,
    password_env: str | None,
) -> StartupPasswordSource | None:
    """Load the startup password from file or development-only environment.

    Args:
        password_file: Path to the mounted password secret file.
        password_env: Development-only password value from environment.

    Returns:
        The resolved password source, or None when no startup password is set.

    Raises:
        StartupPasswordError: If a configured source is unreadable or empty.
    """
    if password_file is not None:
        if password_file == "":
            raise StartupPasswordError("Startup password file path is empty")
        password = _read_password_file(password_file)
        return _required_password(password, STARTUP_FILE_SOURCE)
    if password_env is not None:
        return _required_password(password_env, STARTUP_ENV_SOURCE)
    return None
