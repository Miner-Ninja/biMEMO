"""Tests for password-first startup state detection."""

from dataclasses import dataclass

from backend.security.startup_state import StartupState, determine_startup_state


@dataclass(frozen=True)
class FakeSettings:
    """Minimal settings object for startup-state tests.

    Attributes:
        key_source: Configured key-source mode.
        encrypted_key_file_path: Path to the encrypted master-key file.
        automatic_unlock: Whether automatic unlock is enabled.
        unlock_password_file: Optional automatic unlock secret file path.
        unlock_password: Optional development-only automatic unlock password.
    """

    key_source: str
    encrypted_key_file_path: str = ""
    automatic_unlock: bool = False
    unlock_password_file: str | None = None
    unlock_password: str | None = None


class TestDetermineStartupState:
    """Tests for mapping settings and key-file presence to startup state."""

    def test_file_key_source_is_ready(self) -> None:
        """Verify legacy file key mode maps directly to ready."""
        settings = FakeSettings(key_source="file")

        assert determine_startup_state(settings) == StartupState.READY

    def test_password_mode_without_key_or_password_is_setup(self, tmp_path) -> None:
        """Verify first manual password startup maps to setup."""
        settings = FakeSettings(
            key_source="password",
            encrypted_key_file_path=str(tmp_path / "master.key.enc"),
        )

        assert determine_startup_state(settings) == StartupState.SETUP

    def test_password_mode_with_key_and_no_password_is_locked(self, tmp_path) -> None:
        """Verify existing encrypted key without password maps to locked."""
        encrypted_key_file = tmp_path / "master.key.enc"
        encrypted_key_file.write_text("encrypted-key-placeholder")
        settings = FakeSettings(
            key_source="password",
            encrypted_key_file_path=str(encrypted_key_file),
        )

        assert determine_startup_state(settings) == StartupState.LOCKED

    def test_password_file_source_maps_to_auto_unlock(self, tmp_path) -> None:
        """Verify enabled password file maps to auto-unlock."""
        settings = FakeSettings(
            key_source="password",
            encrypted_key_file_path=str(tmp_path / "master.key.enc"),
            automatic_unlock=True,
            unlock_password_file="/run/secrets/bimemo_unlock_password",
        )

        assert determine_startup_state(settings) == StartupState.AUTO_UNLOCK

    def test_password_env_source_maps_to_auto_unlock(self, tmp_path) -> None:
        """Verify enabled development password maps to auto-unlock."""
        settings = FakeSettings(
            key_source="password",
            encrypted_key_file_path=str(tmp_path / "master.key.enc"),
            automatic_unlock=True,
            unlock_password="development-password",
        )

        assert determine_startup_state(settings) == StartupState.AUTO_UNLOCK

    def test_disabled_automatic_unlock_ignores_password_source(self, tmp_path) -> None:
        """Verify password sources are ignored when automatic unlock is disabled."""
        settings = FakeSettings(
            key_source="password",
            encrypted_key_file_path=str(tmp_path / "master.key.enc"),
            automatic_unlock=False,
            unlock_password_file="/run/secrets/bimemo_unlock_password",
        )

        assert determine_startup_state(settings) == StartupState.SETUP
