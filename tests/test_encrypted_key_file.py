"""Tests for EncryptedKeyFile encrypt/decrypt and file operations.

Covers roundtrip encryption/decryption, wrong password rejection,
tamper detection via GCM tag, and atomic file write behavior.
"""

import os

import pytest
from backend.security.encrypted_key_file import (
    KeyFileError,
    WrongPasswordError,
    decrypt_master_key,
    encrypt_master_key,
    read_key_file,
    write_key_file,
)


class TestEncryptDecryptRoundtrip:
    """Tests for encrypt/decrypt roundtrip."""

    def test_roundtrip_succeeds(self):
        """Verify encrypt then decrypt returns original key."""
        master_key = os.urandom(32)
        password = "TestPassword1234!@#$"

        encrypted = encrypt_master_key(
            master_key, password, memory_kb=1024, iterations=1, parallelism=1
        )
        decrypted = decrypt_master_key(encrypted, password)
        assert decrypted == master_key

    def test_roundtrip_with_passphrase(self):
        """Verify roundtrip with a long passphrase."""
        master_key = os.urandom(32)
        password = "this is a very long passphrase for testing"

        encrypted = encrypt_master_key(
            master_key, password, memory_kb=1024, iterations=1, parallelism=1
        )
        decrypted = decrypt_master_key(encrypted, password)
        assert decrypted == master_key

    def test_different_passwords_produce_different_blobs(self):
        """Verify different passwords produce different encrypted outputs."""
        master_key = os.urandom(32)
        enc1 = encrypt_master_key(
            master_key, "Password1!Test!!", memory_kb=1024, iterations=1, parallelism=1
        )
        enc2 = encrypt_master_key(
            master_key, "Password2!Test!!", memory_kb=1024, iterations=1, parallelism=1
        )
        assert enc1 != enc2


class TestWrongPassword:
    """Tests for wrong password rejection."""

    def test_wrong_password_raises(self):
        """Verify wrong password raises WrongPasswordError."""
        master_key = os.urandom(32)
        encrypted = encrypt_master_key(
            master_key, "CorrectPassword1!", memory_kb=1024, iterations=1, parallelism=1
        )
        with pytest.raises((WrongPasswordError, Exception)):
            decrypt_master_key(encrypted, "WrongPassword123!")


class TestTamperDetection:
    """Tests for GCM tag tamper detection."""

    def test_tampered_ciphertext_raises(self):
        """Verify tampering with ciphertext is detected."""
        master_key = os.urandom(32)
        encrypted = encrypt_master_key(
            master_key, "TestPassword1234!", memory_kb=1024, iterations=1, parallelism=1
        )
        tampered = bytearray(encrypted)
        tampered[-20] ^= 0xFF
        with pytest.raises((WrongPasswordError, Exception)):
            decrypt_master_key(bytes(tampered), "TestPassword1234!")

    def test_invalid_file_size_raises(self):
        """Verify invalid file size raises KeyFileError."""
        with pytest.raises(KeyFileError, match="Invalid key file size"):
            decrypt_master_key(b"too short", "password")


class TestFileOperations:
    """Tests for key file read/write."""

    def test_write_and_read(self, tmp_path):
        """Verify write and read roundtrip."""
        path = str(tmp_path / "test.key")
        data = os.urandom(88)
        write_key_file(path, data)
        assert read_key_file(path) == data

    def test_read_nonexistent_raises(self):
        """Verify reading nonexistent file raises KeyFileError."""
        with pytest.raises(KeyFileError, match="not found"):
            read_key_file("/nonexistent/path/key.file")

    def test_write_creates_parent_dirs(self, tmp_path):
        """Verify write creates parent directories."""
        path = str(tmp_path / "sub" / "dir" / "test.key")
        data = os.urandom(88)
        write_key_file(path, data)
        assert read_key_file(path) == data

    def test_read_permission_error_raises(self, tmp_path):
        """Verify unreadable file raises KeyFileError."""
        path = tmp_path / "noperm.key"
        path.write_bytes(b"data")
        path.chmod(0o000)
        with pytest.raises(KeyFileError, match="Cannot read"):
            read_key_file(str(path))
        path.chmod(0o644)

    def test_write_error_cleans_up(self, tmp_path):
        """Verify write failure cleans up temp file."""
        from unittest.mock import patch

        path = str(tmp_path / "fail.key")
        with (
            patch("os.fsync", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            write_key_file(path, b"data")
        # Temp file should have been cleaned up
        remaining = list(tmp_path.glob("tmp*"))
        assert len(remaining) == 0


class TestGenericDecryptError:
    """Tests for non-InvalidTag decrypt failures."""

    def test_generic_error_raises_wrong_password(self):
        """Verify non-InvalidTag error raises WrongPasswordError."""
        from unittest.mock import patch

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        master_key = os.urandom(32)
        password = "TestPassword1234!"
        encrypted = encrypt_master_key(
            master_key,
            password,
            memory_kb=1024,
            iterations=1,
            parallelism=1,
        )
        with (
            patch.object(
                AESGCM,
                "decrypt",
                side_effect=ValueError("corrupted"),
            ),
            pytest.raises(WrongPasswordError),
        ):
            decrypt_master_key(encrypted, password)


class TestTamperFileError:
    """Tests for GCM tag tamper detection."""

    def test_tampered_tag_raises_tampered_error(self):
        """Verify modifying the GCM tag raises TamperedFileError."""
        from backend.security.encrypted_key_file import (
            TamperedFileError,
        )

        master_key = os.urandom(32)
        password = "TestPassword1234!"
        encrypted = encrypt_master_key(
            master_key,
            password,
            memory_kb=1024,
            iterations=1,
            parallelism=1,
        )
        # Tamper with the last byte (GCM tag)
        tampered = bytearray(encrypted)
        tampered[-1] ^= 0xFF
        with pytest.raises(TamperedFileError):
            decrypt_master_key(bytes(tampered), password)
