"""Tests for Docker password-startup runtime provisioning."""

import os
from pathlib import Path


DOCKER_SECRET_PATH = Path("/run/secrets/bimemo_unlock_password")


class TestDockerPasswordStartupConfig:
    """Tests that Docker test runtime provisions password startup."""

    def test_dev_container_has_unlock_password_secret(self) -> None:
        """Verify dev compose mounted the unlock password secret into the container."""
        assert DOCKER_SECRET_PATH.is_file()

    def test_test_runtime_uses_password_startup(self) -> None:
        """Verify Docker test runtime defaults to password startup."""
        assert os.environ["BIMEMO_KEY_SOURCE"] == "password"
        assert os.environ["BIMEMO_ENCRYPTED_KEY_FILE_PATH"].endswith(
            "test-master.key.enc"
        )
        assert os.environ["BIMEMO_UNLOCK_PASSWORD"] == "test-startup-password"

    def test_test_runtime_has_no_raw_key_startup_path(self) -> None:
        """Verify Docker test runtime does not depend on raw key startup."""
        assert "BIMEMO_KEY_FILE_PATH" not in os.environ
