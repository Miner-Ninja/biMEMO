"""Tests for first-run setup flow.

Covers setup token generation, token validation, password validation,
key file creation, and setup completion behavior.
"""

import os

import pytest
from backend.security.encrypted_key_file import decrypt_master_key
from backend.security.setup import SetupError, SetupManager


class TestNeedsSetup:
    """Tests for setup detection."""

    def test_needs_setup_when_no_key_file(self, tmp_path):
        """Verify needs_setup is True when no key file exists."""
        manager = SetupManager(str(tmp_path / "missing.key"))
        assert manager.needs_setup is True

    def test_no_setup_when_key_file_exists(self, tmp_path):
        """Verify needs_setup is False when key file exists."""
        path = tmp_path / "exists.key"
        path.write_bytes(b"data")
        manager = SetupManager(str(path))
        assert manager.needs_setup is False

    def test_no_setup_after_completion(self, tmp_path):
        """Verify needs_setup is False after setup completes."""
        manager = SetupManager(str(tmp_path / "new.key"))
        manager.generate_setup_token()
        manager.complete_setup(manager.setup_token, "StrongPassword1234!")
        assert manager.needs_setup is False


class TestSetupToken:
    """Tests for setup token generation and validation."""

    def test_generate_returns_token(self, tmp_path):
        """Verify token generation returns a string."""
        manager = SetupManager(str(tmp_path / "test.key"))
        token = manager.generate_setup_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_validate_correct_token(self, tmp_path):
        """Verify correct token validates."""
        manager = SetupManager(str(tmp_path / "test.key"))
        token = manager.generate_setup_token()
        assert manager.validate_token(token) is True

    def test_validate_wrong_token(self, tmp_path):
        """Verify wrong token does not validate."""
        manager = SetupManager(str(tmp_path / "test.key"))
        manager.generate_setup_token()
        assert manager.validate_token("wrong-token") is False

    def test_validate_without_generate(self, tmp_path):
        """Verify validation fails if no token generated."""
        manager = SetupManager(str(tmp_path / "test.key"))
        assert manager.validate_token("anything") is False


class TestCompleteSetup:
    """Tests for setup completion."""

    def test_complete_creates_key_file(self, tmp_path):
        """Verify setup creates the encrypted key file."""
        path = str(tmp_path / "master.key")
        manager = SetupManager(path)
        manager.generate_setup_token()
        manager.complete_setup(manager.setup_token, "StrongPassword1234!")
        assert os.path.exists(path)

    def test_complete_returns_master_key(self, tmp_path):
        """Verify setup returns a 32-byte master key."""
        manager = SetupManager(str(tmp_path / "master.key"))
        manager.generate_setup_token()
        key = manager.complete_setup(manager.setup_token, "StrongPassword1234!")
        assert len(key) == 32

    def test_complete_key_is_decryptable(self, tmp_path):
        """Verify the written key file can be decrypted."""
        path = str(tmp_path / "master.key")
        password = "StrongPassword1234!"
        manager = SetupManager(path)
        manager.generate_setup_token()
        key = manager.complete_setup(manager.setup_token, password)

        with open(path, "rb") as f:
            blob = f.read()
        decrypted = decrypt_master_key(blob, password)
        assert decrypted == key

    def test_double_setup_raises(self, tmp_path):
        """Verify setup cannot run twice."""
        manager = SetupManager(str(tmp_path / "master.key"))
        manager.generate_setup_token()
        manager.complete_setup(manager.setup_token, "StrongPassword1234!")
        with pytest.raises(SetupError, match="already completed"):
            manager.complete_setup("any", "StrongPassword1234!")

    def test_invalid_token_raises(self, tmp_path):
        """Verify invalid token raises SetupError."""
        manager = SetupManager(str(tmp_path / "master.key"))
        manager.generate_setup_token()
        with pytest.raises(SetupError, match="Invalid setup token"):
            manager.complete_setup("wrong", "StrongPassword1234!")


class TestPasswordValidation:
    """Tests for password strength validation."""

    def test_short_password_raises(self, tmp_path):
        """Verify short password is rejected."""
        manager = SetupManager(str(tmp_path / "test.key"))
        manager.generate_setup_token()
        with pytest.raises(SetupError, match="at least 16"):
            manager.complete_setup(manager.setup_token, "short")

    def test_no_uppercase_raises(self, tmp_path):
        """Verify password without uppercase is rejected."""
        manager = SetupManager(str(tmp_path / "test.key"))
        manager.generate_setup_token()
        with pytest.raises(SetupError, match="uppercase"):
            manager.complete_setup(manager.setup_token, "alllowercase1234!")

    def test_no_digit_raises(self, tmp_path):
        """Verify password without digit is rejected."""
        manager = SetupManager(str(tmp_path / "test.key"))
        manager.generate_setup_token()
        with pytest.raises(SetupError, match="digit"):
            manager.complete_setup(manager.setup_token, "NoDigitsHereAtAll!")

    def test_no_special_raises(self, tmp_path):
        """Verify password without special char is rejected."""
        manager = SetupManager(str(tmp_path / "test.key"))
        manager.generate_setup_token()
        with pytest.raises(SetupError, match="special"):
            manager.complete_setup(manager.setup_token, "NoSpecialChars1234")

    def test_passphrase_mode_accepted(self, tmp_path):
        """Verify 24+ char passphrase is accepted."""
        manager = SetupManager(str(tmp_path / "test.key"))
        manager.generate_setup_token()
        key = manager.complete_setup(
            manager.setup_token,
            "this is a long passphrase that is valid",
        )
        assert len(key) == 32
