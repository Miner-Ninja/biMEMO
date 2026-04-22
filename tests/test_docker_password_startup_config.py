"""Tests for Docker password-startup runtime provisioning."""

import os


class TestDockerPasswordStartupConfig:
    """Tests that Docker test runtime provisions password startup."""

    def test_test_runtime_uses_password_startup(self) -> None:
        """Verify Docker test runtime uses password startup."""
        assert os.environ["BIMEMO_KEY_SOURCE"] == "password"
        assert os.environ["BIMEMO_ENCRYPTED_KEY_FILE_PATH"].endswith(
            "test-master.key.enc"
        )
        assert os.environ["BIMEMO_AUTOMATIC_UNLOCK"] == "true"
        assert os.environ["BIMEMO_UNLOCK_PASSWORD"] == "test-startup-password"

    def test_test_runtime_has_no_raw_key_startup_path(self) -> None:
        """Verify Docker test runtime does not depend on raw key startup."""
        assert "BIMEMO_KEY_FILE_PATH" not in os.environ
