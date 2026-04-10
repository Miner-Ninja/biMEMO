"""Pluggable key source abstraction for obtaining cryptographic keys.

Provides a protocol for key retrieval and implementations for different
key storage backends. The initial implementation reads from a file on disk.
The interface supports future backends (e.g., HashiCorp Vault).
"""

from backend.security.keysource.file import FileKeySource
from backend.security.keysource.protocol import (
    KeySource,
    KeySourceError,
    get_key_source,
)


__all__ = [
    "FileKeySource",
    "KeySource",
    "KeySourceError",
    "get_key_source",
]
