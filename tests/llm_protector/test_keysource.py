"""Tests for KeySource protocol and FileKeySource implementation.

Covers valid key loading, missing file, wrong permissions, wrong file size,
and the factory function for selecting key source implementations.
"""

import os
import secrets

import pytest
from backend.security.keysource import (
    FileKeySource,
    KeySourceError,
    PasswordKeySource,
    get_key_source,
)


VALID_KEY = secrets.token_bytes(32)


@pytest.fixture
def key_file(tmp_path):
    """Create a valid key file with correct permissions."""
    path = tmp_path / "master.key"
    path.write_bytes(VALID_KEY)
    os.chmod(path, 0o600)
    return str(path)


@pytest.fixture
def key_file_wrong_perms(tmp_path):
    """Create a key file with insecure permissions (644)."""
    path = tmp_path / "master.key"
    path.write_bytes(VALID_KEY)
    os.chmod(path, 0o644)
    return str(path)


@pytest.fixture
def key_file_wrong_size(tmp_path):
    """Create a key file with wrong number of bytes."""
    path = tmp_path / "master.key"
    path.write_bytes(b"too_short")
    os.chmod(path, 0o600)
    return str(path)


class TestFileKeySource:
    """Tests for FileKeySource implementation."""

    @pytest.mark.asyncio
    async def test_valid_key_file(self, key_file):
        """Load key from a valid file with correct permissions."""
        source = FileKeySource(file_path=key_file)
        key = await source.get_key()
        assert key == VALID_KEY
        assert len(key) == 32

    @pytest.mark.asyncio
    async def test_missing_file(self, tmp_path):
        """Raise error when key file does not exist."""
        source = FileKeySource(file_path=str(tmp_path / "nonexistent.key"))
        with pytest.raises(KeySourceError, match="Key file not found"):
            await source.get_key()

    @pytest.mark.asyncio
    async def test_wrong_permissions(self, key_file_wrong_perms):
        """Raise error when key file has insecure permissions."""
        source = FileKeySource(file_path=key_file_wrong_perms)
        with pytest.raises(KeySourceError, match="Insecure key file permissions"):
            await source.get_key()

    @pytest.mark.asyncio
    async def test_wrong_size(self, key_file_wrong_size):
        """Raise error when key file has unexpected size."""
        source = FileKeySource(file_path=key_file_wrong_size)
        with pytest.raises(KeySourceError, match="Invalid key file size"):
            await source.get_key()


class TestGetKeySource:
    """Tests for the key source factory function."""

    def test_file_source(self, key_file):
        """Create a FileKeySource from factory with type='file'."""
        source = get_key_source("file", file_path=key_file)
        assert isinstance(source, FileKeySource)
        assert source.file_path == key_file

    def test_password_source(self, tmp_path):
        """Create a PasswordKeySource from factory with type='password'."""
        encrypted_path = tmp_path / "master.key.enc"
        source = get_key_source(
            "password",
            encrypted_key_file_path=str(encrypted_path),
            password="StrongPassword1234!",
        )
        assert isinstance(source, PasswordKeySource)
        assert source.encrypted_key_file_path == str(encrypted_path)

    def test_file_source_missing_path(self):
        """Raise error when file_path is not provided for file source."""
        with pytest.raises(KeySourceError, match="file_path is required"):
            get_key_source("file")

    def test_unknown_source_type(self):
        """Raise error for unknown key source type."""
        with pytest.raises(KeySourceError, match="Unknown key source type"):
            get_key_source("vault")

    def test_empty_source_type(self):
        """Raise error for empty key source type."""
        with pytest.raises(KeySourceError, match="Key source type is required"):
            get_key_source("")
