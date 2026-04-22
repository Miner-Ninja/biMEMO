"""Tests for password-based master-key source."""

import os

import pytest
from backend.security.encrypted_key_file import (
    decrypt_master_key,
    encrypt_master_key,
    read_key_file,
)
from backend.security.keysource import (
    KeySourceError,
    PasswordKeySource,
    get_key_source,
)


FAST_MEMORY_KB = 1024
FAST_ITERATIONS = 1
FAST_PARALLELISM = 1
STARTUP_PASSWORD = "StrongPassword1234!"


def _fast_encrypt(master_key: bytes, password: str) -> bytes:
    """Encrypt a master key with fast test-only Argon2 parameters."""
    return encrypt_master_key(
        master_key,
        password,
        memory_kb=FAST_MEMORY_KB,
        iterations=FAST_ITERATIONS,
        parallelism=FAST_PARALLELISM,
    )


@pytest.fixture
def fast_password_keysource(monkeypatch):
    """Patch PasswordKeySource encryption to use fast test parameters."""
    import backend.security.keysource.password as password_module

    monkeypatch.setattr(password_module, "encrypt_master_key", _fast_encrypt)


class TestPasswordKeySource:
    """Tests for password-based key setup and unlock."""

    @pytest.mark.asyncio
    async def test_first_setup_creates_encrypted_key_file(
        self,
        tmp_path,
        fast_password_keysource,
    ):
        """Create encrypted key file when it does not exist."""
        del fast_password_keysource
        encrypted_path = tmp_path / "master.key.enc"
        source = PasswordKeySource(
            encrypted_key_file_path=str(encrypted_path),
            password=STARTUP_PASSWORD,
        )

        master_key = await source.get_key()

        assert len(master_key) == 32
        assert encrypted_path.exists()
        assert encrypted_path.read_bytes() != master_key
        assert decrypt_master_key(read_key_file(str(encrypted_path)), STARTUP_PASSWORD)

    @pytest.mark.asyncio
    async def test_unlock_existing_encrypted_key_file(self, tmp_path):
        """Unlock existing encrypted key file with the correct password."""
        master_key = os.urandom(32)
        encrypted_path = tmp_path / "master.key.enc"
        encrypted_path.write_bytes(_fast_encrypt(master_key, STARTUP_PASSWORD))
        source = PasswordKeySource(
            encrypted_key_file_path=str(encrypted_path),
            password=STARTUP_PASSWORD,
        )

        assert await source.get_key() == master_key

    @pytest.mark.asyncio
    async def test_wrong_password_raises(self, tmp_path):
        """Reject wrong password without returning a master key."""
        master_key = os.urandom(32)
        encrypted_path = tmp_path / "master.key.enc"
        encrypted_path.write_bytes(_fast_encrypt(master_key, STARTUP_PASSWORD))
        source = PasswordKeySource(
            encrypted_key_file_path=str(encrypted_path),
            password="WrongPassword1234!",
        )

        with pytest.raises(KeySourceError, match="Failed to unlock"):
            await source.get_key()

    @pytest.mark.asyncio
    async def test_corrupted_key_file_raises(self, tmp_path):
        """Reject corrupted encrypted key file."""
        encrypted_path = tmp_path / "master.key.enc"
        encrypted_path.write_bytes(b"corrupted")
        source = PasswordKeySource(
            encrypted_key_file_path=str(encrypted_path),
            password=STARTUP_PASSWORD,
        )

        with pytest.raises(KeySourceError, match="Failed to unlock"):
            await source.get_key()

    @pytest.mark.asyncio
    async def test_missing_password_raises(self, tmp_path):
        """Reject missing startup password."""
        encrypted_path = tmp_path / "master.key.enc"
        source = PasswordKeySource(
            encrypted_key_file_path=str(encrypted_path),
            password="",
        )

        with pytest.raises(KeySourceError, match="password is required"):
            await source.get_key()


class TestPasswordKeySourceFactory:
    """Tests for factory support of password key source."""

    def test_password_source(self, tmp_path):
        """Create a PasswordKeySource from factory with type='password'."""
        encrypted_path = tmp_path / "master.key.enc"
        source = get_key_source(
            "password",
            encrypted_key_file_path=str(encrypted_path),
            password=STARTUP_PASSWORD,
        )

        assert isinstance(source, PasswordKeySource)
        assert source.encrypted_key_file_path == str(encrypted_path)

    def test_password_source_missing_path(self):
        """Raise error when encrypted key path is missing."""
        with pytest.raises(KeySourceError, match="encrypted_key_file_path"):
            get_key_source("password", password=STARTUP_PASSWORD)

    def test_password_source_missing_password(self, tmp_path):
        """Raise error when password is missing."""
        encrypted_path = tmp_path / "master.key.enc"

        with pytest.raises(KeySourceError, match="password is required"):
            get_key_source(
                "password",
                encrypted_key_file_path=str(encrypted_path),
            )
