"""Password-based key source backed by an encrypted master-key file."""

import logging
import os
import secrets

from backend.security.encrypted_key_file import (
    KeyFileError,
    decrypt_master_key,
    encrypt_master_key,
    read_key_file,
    write_key_file,
)
from backend.security.keysource.protocol import EXPECTED_KEY_LENGTH, KeySourceError
from backend.security.master_key import MASTER_KEY_LENGTH


logger = logging.getLogger(__name__)


class PasswordKeySource:
    """Read or create a master key through a password-encrypted key file.

    Attributes:
        encrypted_key_file_path: Path to the encrypted master-key file.
        password: Startup password used to create or unlock the encrypted key.
    """

    def __init__(self, encrypted_key_file_path: str, password: str) -> None:
        """Initialize the password key source.

        Args:
            encrypted_key_file_path: Path to the encrypted master-key file.
            password: Startup password used for encryption or decryption.
        """
        self.encrypted_key_file_path = encrypted_key_file_path
        self.password = password

    async def get_key(self) -> bytes:
        """Return the raw master key after setup or unlock.

        Returns:
            Exactly 32 raw bytes.

        Raises:
            KeySourceError: If setup or unlock fails.
        """
        if not self.password:
            raise KeySourceError("password is required for password key source")
        if self._encrypted_key_file_exists():
            return self._unlock_existing_key()
        return self._create_encrypted_key()

    def _encrypted_key_file_exists(self) -> bool:
        """Return whether the encrypted master-key file already exists."""
        return os.path.isfile(self.encrypted_key_file_path)

    def _create_encrypted_key(self) -> bytes:
        """Create and persist a new encrypted master key."""
        master_key = secrets.token_bytes(MASTER_KEY_LENGTH)
        encrypted_blob = encrypt_master_key(master_key, self.password)
        try:
            write_key_file(self.encrypted_key_file_path, encrypted_blob)
        except KeyFileError as e:
            raise KeySourceError("Failed to write encrypted key file") from e
        logger.info(
            "Created encrypted master-key file: %s",
            self.encrypted_key_file_path,
        )
        return master_key

    def _unlock_existing_key(self) -> bytes:
        """Decrypt and validate the existing encrypted master-key file."""
        try:
            encrypted_blob = read_key_file(self.encrypted_key_file_path)
            master_key = decrypt_master_key(encrypted_blob, self.password)
        except KeyFileError as e:
            raise KeySourceError("Failed to unlock encrypted key file") from e
        self._validate_master_key(master_key)
        logger.info(
            "Unlocked encrypted master-key file: %s",
            self.encrypted_key_file_path,
        )
        return master_key

    @staticmethod
    def _validate_master_key(master_key: bytes) -> None:
        """Ensure decrypted master key has the required byte length."""
        if len(master_key) != EXPECTED_KEY_LENGTH:
            raise KeySourceError("Invalid decrypted master key length")
