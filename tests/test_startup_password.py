"""Tests for startup password source loading."""

import pytest
from backend.security.startup_password import (
    STARTUP_ENV_SOURCE,
    STARTUP_FILE_SOURCE,
    StartupPasswordError,
    load_startup_password,
)


class TestLoadStartupPassword:
    """Tests for startup password loading from configured sources."""

    def test_password_file_wins_over_env(self, tmp_path):
        """Verify password file takes precedence over environment value."""
        password_file = tmp_path / "bimemo_unlock_password"
        password_file.write_text("file-password")

        source = load_startup_password(
            password_file=str(password_file),
            password_env="env-password",
        )

        assert source is not None
        assert source.password == "file-password"
        assert source.source == STARTUP_FILE_SOURCE

    def test_password_env_used_when_file_missing(self):
        """Verify development env password is used when file is unset."""
        source = load_startup_password(
            password_file=None,
            password_env="env-password",
        )

        assert source is not None
        assert source.password == "env-password"
        assert source.source == STARTUP_ENV_SOURCE

    def test_missing_password_returns_none(self):
        """Verify missing password sources return None."""
        assert load_startup_password(password_file=None, password_env=None) is None

    def test_password_file_trims_one_lf(self, tmp_path):
        """Verify one trailing LF is removed from file passwords."""
        password_file = tmp_path / "bimemo_unlock_password"
        password_file.write_text("file-password\n")

        source = load_startup_password(
            password_file=str(password_file),
            password_env=None,
        )

        assert source is not None
        assert source.password == "file-password"

    def test_password_file_trims_one_crlf(self, tmp_path):
        """Verify one trailing CRLF is removed from file passwords."""
        password_file = tmp_path / "bimemo_unlock_password"
        password_file.write_text("file-password\r\n")

        source = load_startup_password(
            password_file=str(password_file),
            password_env=None,
        )

        assert source is not None
        assert source.password == "file-password"

    def test_password_file_preserves_extra_newline(self, tmp_path):
        """Verify only one trailing newline is removed from file passwords."""
        password_file = tmp_path / "bimemo_unlock_password"
        password_file.write_text("file-password\n\n")

        source = load_startup_password(
            password_file=str(password_file),
            password_env=None,
        )

        assert source is not None
        assert source.password == "file-password\n"

    def test_empty_password_file_raises(self, tmp_path):
        """Verify empty password file raises a clear error."""
        password_file = tmp_path / "bimemo_unlock_password"
        password_file.write_text("")

        with pytest.raises(StartupPasswordError, match="empty"):
            load_startup_password(
                password_file=str(password_file),
                password_env=None,
            )

    def test_empty_password_env_raises(self):
        """Verify explicit empty environment password raises a clear error."""
        with pytest.raises(StartupPasswordError, match="empty"):
            load_startup_password(password_file=None, password_env="")

    def test_unreadable_password_file_raises(self, tmp_path):
        """Verify unreadable password file raises a clear error."""
        missing_file = tmp_path / "missing"

        with pytest.raises(StartupPasswordError, match="Cannot read"):
            load_startup_password(
                password_file=str(missing_file),
                password_env="env-password",
            )

    def test_empty_password_file_path_raises(self):
        """Verify explicit empty password-file path raises a clear error."""
        with pytest.raises(StartupPasswordError, match="path is empty"):
            load_startup_password(password_file="", password_env="env-password")
