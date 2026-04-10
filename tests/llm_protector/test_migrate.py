"""Tests for the migration CLI: Argon2id-encrypted key → plain keyfile.

Covers successful migration, wrong password handling, and output
file permissions verification.
"""

import os
import stat

import pytest
from backend.security.encrypted_key_file import encrypt_master_key
from backend.security.migrate import migrate_to_keyfile


# Use minimal Argon2 params for fast tests.
FAST_MEMORY_KB = 1024
FAST_ITERATIONS = 1
FAST_PARALLELISM = 1


def _create_encrypted_file(
    tmp_path: pytest.TempPathFactory, master_key: bytes, password: str
) -> str:
    """Helper: encrypt a master key and write to a temp file."""
    blob = encrypt_master_key(
        master_key,
        password,
        memory_kb=FAST_MEMORY_KB,
        iterations=FAST_ITERATIONS,
        parallelism=FAST_PARALLELISM,
    )
    enc_path = str(tmp_path / "encrypted.key")
    with open(enc_path, "wb") as f:
        f.write(blob)
    return enc_path


class TestMigrateToKeyfile:
    """Tests for migrate_to_keyfile function."""

    def test_successful_migration(self, tmp_path: pytest.TempPathFactory) -> None:
        """Decrypt encrypted key file and write plain 32-byte keyfile."""
        master_key = os.urandom(32)
        password = "test-password-123"
        enc_path = _create_encrypted_file(tmp_path, master_key, password)
        out_path = str(tmp_path / "plain.key")

        migrate_to_keyfile(enc_path, out_path, password=password)

        with open(out_path, "rb") as output_file:
            result = output_file.read()

        assert result == master_key
        assert len(result) == 32

    def test_output_file_permissions(self, tmp_path: pytest.TempPathFactory) -> None:
        """Output file has chmod 600 (owner read/write only)."""
        master_key = os.urandom(32)
        password = "perm-check"
        enc_path = _create_encrypted_file(tmp_path, master_key, password)
        out_path = str(tmp_path / "plain.key")

        migrate_to_keyfile(enc_path, out_path, password=password)

        mode = os.stat(out_path).st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_wrong_password_exits(self, tmp_path: pytest.TempPathFactory) -> None:
        """Wrong password causes SystemExit."""
        master_key = os.urandom(32)
        enc_path = _create_encrypted_file(tmp_path, master_key, "correct")
        out_path = str(tmp_path / "plain.key")

        with pytest.raises(SystemExit):
            migrate_to_keyfile(enc_path, out_path, password="wrong")

        assert not os.path.exists(out_path)

    def test_missing_encrypted_file_exits(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Missing encrypted file causes SystemExit."""
        out_path = str(tmp_path / "plain.key")

        with pytest.raises(SystemExit):
            migrate_to_keyfile(
                str(tmp_path / "nonexistent.key"), out_path, password="x"
            )

    def test_creates_parent_directories(self, tmp_path: pytest.TempPathFactory) -> None:
        """Output parent directories are created if missing."""
        master_key = os.urandom(32)
        password = "mkdir-test"
        enc_path = _create_encrypted_file(tmp_path, master_key, password)
        out_path = str(tmp_path / "nested" / "dirs" / "plain.key")

        migrate_to_keyfile(enc_path, out_path, password=password)

        with open(out_path, "rb") as output_file:
            assert output_file.read() == master_key

    def test_no_temp_file_left_on_success(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """No .tmp file remains after successful migration."""
        master_key = os.urandom(32)
        password = "cleanup-test"
        enc_path = _create_encrypted_file(tmp_path, master_key, password)
        out_path = str(tmp_path / "plain.key")

        migrate_to_keyfile(enc_path, out_path, password=password)

        tmp_file = out_path + ".tmp"
        assert not os.path.exists(tmp_file)
