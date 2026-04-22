"""Tests for process target master-key loading."""

import os
from dataclasses import dataclass

import pytest
from backend.processes.targets import _load_master_key
from backend.security.encrypted_key_file import encrypt_master_key
from backend.security.keysource import KeySourceError


FAST_MEMORY_KB = 1024
FAST_ITERATIONS = 1
FAST_PARALLELISM = 1
VALID_STARTUP_SECRET = "StrongSecret1234!"


@dataclass
class KeyLoadingSettings:
    """Minimal settings object for master-key loading tests.

    Attributes:
        key_source: Selected key source type.
        key_file_path: Path to a raw key file.
        encrypted_key_file_path: Path to an encrypted key file.
        unlock_password_file: Optional startup secret file path.
        unlock_password: Optional development-only startup secret.
    """

    key_source: str
    key_file_path: str = ""
    encrypted_key_file_path: str = ""
    unlock_password_file: str | None = None
    unlock_password: str | None = None


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


class TestLoadMasterKey:
    """Tests for loading master keys from process target settings."""

    @pytest.mark.asyncio
    async def test_loads_raw_file_key(self, tmp_path):
        """Load existing raw key file in file mode."""
        master_key = os.urandom(32)
        key_path = tmp_path / "master.key"
        key_path.write_bytes(master_key)
        key_path.chmod(0o600)

        loaded_key = await _load_master_key(
            KeyLoadingSettings(
                key_source="file",
                key_file_path=str(key_path),
            )
        )

        assert loaded_key == master_key

    @pytest.mark.asyncio
    async def test_loads_password_key_from_secret_file(self, tmp_path):
        """Load encrypted key with password read from secret file."""
        master_key = os.urandom(32)
        encrypted_path = tmp_path / "master.key.enc"
        secret_path = tmp_path / "bimemo_unlock_password"
        encrypted_path.write_bytes(_fast_encrypt(master_key, VALID_STARTUP_SECRET))
        secret_path.write_text(f"{VALID_STARTUP_SECRET}\n")

        loaded_key = await _load_master_key(
            KeyLoadingSettings(
                key_source="password",
                encrypted_key_file_path=str(encrypted_path),
                unlock_password_file=str(secret_path),
                unlock_password="wrong-dev-secret",
            )
        )

        assert loaded_key == master_key

    @pytest.mark.asyncio
    async def test_password_env_creates_missing_encrypted_key(
        self,
        tmp_path,
        fast_password_keysource,
    ):
        """Create encrypted key file when password env is supplied."""
        del fast_password_keysource
        encrypted_path = tmp_path / "master.key.enc"

        loaded_key = await _load_master_key(
            KeyLoadingSettings(
                key_source="password",
                encrypted_key_file_path=str(encrypted_path),
                unlock_password=VALID_STARTUP_SECRET,
            )
        )

        assert len(loaded_key) == 32
        assert encrypted_path.exists()
        assert encrypted_path.read_bytes() != loaded_key

    @pytest.mark.asyncio
    async def test_password_mode_requires_startup_password(self, tmp_path):
        """Reject password mode when no startup password source is configured."""
        encrypted_path = tmp_path / "master.key.enc"

        with pytest.raises(KeySourceError, match="Startup password is required"):
            await _load_master_key(
                KeyLoadingSettings(
                    key_source="password",
                    encrypted_key_file_path=str(encrypted_path),
                )
            )
