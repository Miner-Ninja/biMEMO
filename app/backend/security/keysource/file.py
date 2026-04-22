"""Internal file-based key source that reads a raw key from disk.

Reads exactly 32 bytes from a specified file path. Validates file
permissions (chmod 600) and file size before returning the key.
Public Docker installs use the password key source. This source remains only
for low-level internal tests and controlled repair work where an operator
already has a raw 32-byte SQLCipher key.
"""

import logging
import os
import stat

from backend.security.keysource.protocol import EXPECTED_KEY_LENGTH, KeySourceError


logger = logging.getLogger(__name__)


class FileKeySource:
    """Read a cryptographic key from a file on disk.

    Validates that the file exists, has correct permissions (0600),
    and contains exactly 32 bytes.

    Attributes:
        file_path: Absolute path to the key file.
    """

    def __init__(self, file_path: str) -> None:
        """Initialize the file key source.

        Args:
            file_path: Path to the key file containing 32 raw bytes.
        """
        self.file_path = file_path

    def _path(self) -> str:
        """Return the configured key-file path."""
        return self.file_path

    def _file_stat(self) -> os.stat_result:
        """Return file metadata for the configured key file."""
        return os.stat(self._path())

    async def get_key(self) -> bytes:
        """Read the key from the configured file path.

        Returns:
            Exactly 32 raw bytes.

        Raises:
            KeySourceError: If the file is missing, has wrong permissions,
                or contains an unexpected number of bytes.
        """
        self._validate_file_exists()
        self._validate_permissions()
        return self._read_key()

    def _validate_file_exists(self) -> None:
        """Check that the key file exists at the configured path."""
        if not os.path.isfile(self._path()):
            raise KeySourceError(f"Key file not found: {self._path()}")

    def _validate_permissions(self) -> None:
        """Check that the key file has chmod 600 (owner read/write only)."""
        file_stat = self._file_stat()
        mode = stat.S_IMODE(file_stat.st_mode)
        if mode != 0o600:
            raise KeySourceError(
                f"Insecure key file permissions: {mode:04o} (expected 0600). "
                f"Fix with: chmod 600 {self._path()}"
            )

    def _read_key(self) -> bytes:
        """Read and validate the key bytes from the file."""
        path = self._path()
        with open(path, "rb") as f:
            key_data = f.read()

        if len(key_data) != EXPECTED_KEY_LENGTH:
            raise KeySourceError(
                f"Invalid key file size: {len(key_data)} bytes "
                f"(expected {EXPECTED_KEY_LENGTH}). "
                f"File: {path}"
            )

        logger.info("Key loaded from file: %s", path)
        return key_data
