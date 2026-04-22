"""Tests for Signing Proxy initial password seeding."""

import pytest
from backend.ipc.models import DBResult
from backend.processes.signing_proxy import main as signing_main
from backend.processes.signing_proxy.main import _seed_initial_password


VALID_STARTUP_SECRET = "StrongSecret1234!"


class FakeDBClient:
    """Fake DB client that records password-hash operations.

    Attributes:
        has_password: Whether a password hash already exists.
        stored_hash: Password hash written by the seeding function.
    """

    def __init__(
        self,
        *,
        has_password: bool = False,
        existing_hash: str = "",
    ) -> None:
        """Initialize fake DB client state."""
        self.has_password = has_password
        self.stored_hash: str | None = existing_hash or None

    async def send_and_receive(self, operation):
        """Return DBResult values for password hash operations."""
        if operation.operation == "get_password_hash":
            return DBResult(
                request_id=operation.request_id,
                status="success",
                data={
                    "exists": self.has_password,
                    "password_hash": self.stored_hash,
                },
            )
        if operation.operation == "store_password_hash":
            self.stored_hash = operation.parameters["password_hash"]
            self.has_password = True
            return DBResult(
                request_id=operation.request_id,
                status="success",
                data={},
            )
        raise AssertionError(f"Unexpected operation: {operation.operation}")


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    """Reset settings singleton and local legacy password around every test."""
    from backend.config import reset_settings

    monkeypatch.delenv("BIMEMO_ADMIN_PASSWORD", raising=False)
    reset_settings()
    yield
    reset_settings()


class TestSeedInitialPassword:
    """Tests for initial UI password seeding sources."""

    @pytest.mark.asyncio
    async def test_seeds_from_startup_password_file(self, monkeypatch, tmp_path):
        """Seed UI password from password-startup secret file."""
        secret_file = tmp_path / "bimemo_unlock_password"
        secret_file.write_text(f"{VALID_STARTUP_SECRET}\n")
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            str(tmp_path / "master.key.enc"),
        )
        monkeypatch.setenv("BIMEMO_UNLOCK_PASSWORD_FILE", str(secret_file))
        db_client = FakeDBClient()

        await _seed_initial_password(db_client)

        assert db_client.stored_hash is not None
        assert signing_main._ph.verify(db_client.stored_hash, VALID_STARTUP_SECRET)

    @pytest.mark.asyncio
    async def test_existing_password_is_not_replaced(self, monkeypatch, tmp_path):
        """Verify existing UI password is not replaced when it matches startup."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            str(tmp_path / "master.key.enc"),
        )
        monkeypatch.setenv("BIMEMO_UNLOCK_PASSWORD", VALID_STARTUP_SECRET)
        existing_hash = signing_main._ph.hash(VALID_STARTUP_SECRET)
        db_client = FakeDBClient(
            has_password=True,
            existing_hash=existing_hash,
        )

        await _seed_initial_password(db_client)

        assert db_client.stored_hash == existing_hash

    @pytest.mark.asyncio
    async def test_existing_password_mismatch_raises(self, monkeypatch, tmp_path):
        """Reject startup when startup password differs from stored UI password."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            str(tmp_path / "master.key.enc"),
        )
        monkeypatch.setenv("BIMEMO_UNLOCK_PASSWORD", VALID_STARTUP_SECRET)
        db_client = FakeDBClient(
            has_password=True,
            existing_hash=signing_main._ph.hash("DifferentSecret1234!"),
        )

        with pytest.raises(RuntimeError, match="does not match"):
            await _seed_initial_password(db_client)

    @pytest.mark.asyncio
    async def test_manual_startup_password_is_verified_against_existing_hash(self):
        """Verify manual startup password must match stored UI password."""
        db_client = FakeDBClient(
            has_password=True,
            existing_hash=signing_main._ph.hash(VALID_STARTUP_SECRET),
        )

        await _seed_initial_password(db_client, VALID_STARTUP_SECRET)

        assert db_client.stored_hash is not None

    @pytest.mark.asyncio
    async def test_legacy_admin_password_wins(self, monkeypatch, tmp_path):
        """Prefer legacy admin env secret over startup secret during transition."""
        legacy_secret = "LegacySecret1234!"
        monkeypatch.setenv("BIMEMO_ADMIN_PASSWORD", legacy_secret)
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            str(tmp_path / "master.key.enc"),
        )
        monkeypatch.setenv("BIMEMO_UNLOCK_PASSWORD", VALID_STARTUP_SECRET)
        db_client = FakeDBClient()

        await _seed_initial_password(db_client)

        assert db_client.stored_hash is not None
        assert signing_main._ph.verify(db_client.stored_hash, legacy_secret)

    @pytest.mark.asyncio
    async def test_missing_startup_password_skips_seed(self, monkeypatch, tmp_path):
        """Skip seeding when password mode has no startup password source."""
        monkeypatch.setenv("BIMEMO_KEY_SOURCE", "password")
        monkeypatch.setenv(
            "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
            str(tmp_path / "master.key.enc"),
        )
        db_client = FakeDBClient()

        await _seed_initial_password(db_client)

        assert db_client.stored_hash is None
