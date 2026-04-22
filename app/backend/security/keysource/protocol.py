"""KeySource protocol and factory for pluggable key retrieval.

Defines the KeySource protocol that all key source implementations must
follow. Provides a factory function to instantiate the correct implementation
based on a source type string. Used by processes that need cryptographic
keys at startup (e.g., DB Proxy for SQLCipher).
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable


logger = logging.getLogger(__name__)

# --- Supported key source types ---
SUPPORTED_KEY_SOURCES = ("file", "password")
EXPECTED_KEY_LENGTH = 32


def _supported_sources_text() -> str:
    """Return the supported key source names as a comma-separated string."""
    return ", ".join(SUPPORTED_KEY_SOURCES)


def _supported_sources_detail() -> str:
    """Return the standard supported-sources detail text."""
    return f"Supported values: {_supported_sources_text()}"


def _unknown_key_source_error(source_type: str) -> KeySourceError:
    """Build the standard unknown-key-source error."""
    return KeySourceError(
        f"Unknown key source type: '{source_type}'. " + _supported_sources_detail()
    )


class KeySourceError(Exception):
    """Raised when key retrieval fails."""


@runtime_checkable
class KeySource(Protocol):
    """Protocol for pluggable key source implementations.

    Implementations must provide an async `get_key` method that returns
    exactly 32 raw bytes suitable for SQLCipher encryption.
    """

    async def get_key(self) -> bytes:
        """Retrieve the cryptographic key.

        Returns:
            Exactly 32 raw bytes.

        Raises:
            KeySourceError: If key retrieval fails.
        """
        ...


def get_key_source(source_type: str, **kwargs: str) -> KeySource:
    """Create a KeySource instance based on the source type.

    Args:
        source_type: Type of key source. Supported: "file", "password".
        **kwargs: Implementation-specific arguments passed to the constructor.

    Returns:
        A KeySource implementation instance.

    Raises:
        KeySourceError: If source_type is unknown or missing.
    """
    if not source_type:
        raise KeySourceError(
            "Key source type is required. " + _supported_sources_detail()
        )

    if source_type == "file":
        from backend.security.keysource.file import FileKeySource

        file_path = kwargs.get("file_path")
        if not file_path:
            raise KeySourceError("file_path is required for file key source")
        return FileKeySource(file_path=file_path)

    if source_type == "password":
        from backend.security.keysource.password import PasswordKeySource

        encrypted_key_file_path = kwargs.get("encrypted_key_file_path")
        password = kwargs.get("password")
        if not encrypted_key_file_path:
            raise KeySourceError(
                "encrypted_key_file_path is required for password key source"
            )
        if not password:
            raise KeySourceError("password is required for password key source")
        return PasswordKeySource(
            encrypted_key_file_path=encrypted_key_file_path,
            password=password,
        )

    raise _unknown_key_source_error(source_type)
