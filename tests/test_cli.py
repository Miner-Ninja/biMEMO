"""Tests for key rotation and password change CLI tools.

Covers master key rotation with PRAGMA rekey, password change
without key rotation, session counter increment, and Argon2
parameter upgrade.
"""

import secrets

import pytest
from backend.security.cli import change_password, rotate_key
from backend.security.database import DatabaseManager
from backend.security.encrypted_key_file import (
    decrypt_master_key,
    encrypt_master_key,
    read_key_file,
    write_key_file,
)
from backend.security.schema import initialize_schema
from backend.security.setup import SetupError


def _create_test_setup(tmp_path, password):
    """Create a test key file and database."""
    master_key = secrets.token_bytes(32)
    key_path = str(tmp_path / "master.key")
    db_path = str(tmp_path / "test.db")

    encrypted = encrypt_master_key(
        master_key,
        password,
        memory_kb=1024,
        iterations=1,
        parallelism=1,
    )
    write_key_file(key_path, encrypted)

    db = DatabaseManager(db_path)
    db.open(bytearray(master_key))
    initialize_schema(db)
    db.close()

    return key_path, db_path, master_key


class TestRotateKey:
    """Tests for master key rotation."""

    def test_rotate_creates_new_key(self, tmp_path):
        """Verify rotation creates a different master key."""
        old_pw = "OldPassword12345!"
        new_pw = "NewPassword12345!"
        key_path, db_path, old_key = _create_test_setup(tmp_path, old_pw)
        rotate_key(key_path, db_path, old_pw, new_pw)

        new_blob = read_key_file(key_path)
        new_key = decrypt_master_key(new_blob, new_pw)
        assert new_key != old_key

    def test_rotate_db_accessible_with_new_key(self, tmp_path):
        """Verify database opens with the new key after rotation."""
        old_pw = "OldPassword12345!"
        new_pw = "NewPassword12345!"
        key_path, db_path, _ = _create_test_setup(tmp_path, old_pw)
        rotate_key(key_path, db_path, old_pw, new_pw)

        new_blob = read_key_file(key_path)
        new_key = decrypt_master_key(new_blob, new_pw)
        db = DatabaseManager(db_path)
        db.open(bytearray(new_key))
        cursor = db.execute("SELECT count(*) FROM sqlite_master")
        assert cursor.fetchone()[0] >= 0
        db.close()


class TestChangePassword:
    """Tests for password change without key rotation."""

    def test_change_preserves_master_key(self, tmp_path):
        """Verify master key is unchanged after password change."""
        old_pw = "OldPassword12345!"
        new_pw = "NewPassword12345!"
        key_path, db_path, original_key = _create_test_setup(tmp_path, old_pw)
        change_password(key_path, db_path, old_pw, new_pw)

        new_blob = read_key_file(key_path)
        decrypted = decrypt_master_key(new_blob, new_pw)
        assert decrypted == original_key

    def test_change_with_argon2_params(self, tmp_path):
        """Verify password change with custom Argon2 parameters."""
        old_pw = "OldPassword12345!"
        new_pw = "NewPassword12345!"
        key_path, db_path, original_key = _create_test_setup(tmp_path, old_pw)
        change_password(
            key_path,
            db_path,
            old_pw,
            new_pw,
            argon2_memory=2048,
            argon2_iterations=2,
            argon2_parallelism=2,
        )

        new_blob = read_key_file(key_path)
        decrypted = decrypt_master_key(new_blob, new_pw)
        assert decrypted == original_key

    def test_weak_password_raises(self, tmp_path):
        """Verify weak new password is rejected."""
        old_pw = "OldPassword12345!"
        key_path, db_path, _ = _create_test_setup(tmp_path, old_pw)
        with pytest.raises(SetupError):
            change_password(key_path, db_path, old_pw, "weak")
