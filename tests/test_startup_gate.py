"""Tests for the password-first startup gate."""

import asyncio

from backend.processes.startup_gate import create_startup_gate_app
from backend.security.startup_state import StartupState
from starlette.testclient import TestClient


class DummyServer:
    """Minimal uvicorn server stand-in for endpoint tests.

    Attributes:
        should_exit: Flag set by the startup gate after password submission.
    """

    should_exit = False


def _make_client(state: StartupState) -> tuple[TestClient, asyncio.Future[str]]:
    """Create a startup-gate test client and its password future."""
    event_loop = asyncio.new_event_loop()
    password_future = event_loop.create_future()
    app = create_startup_gate_app(
        state=state,
        password_future=password_future,
    )
    app.state.startup_gate_server = DummyServer()
    return TestClient(app), password_future


class TestStartupGateStatus:
    """Tests for startup-gate status and health responses."""

    def test_setup_status_requires_setup(self) -> None:
        """Verify setup state reports first setup is needed."""
        client, _ = _make_client(StartupState.SETUP)

        response = client.get("/auth/status")

        assert response.status_code == 200
        assert response.json()["startup_state"] == "setup"
        assert response.json()["needs_setup"] is True
        assert response.json()["authenticated"] is False

    def test_locked_status_does_not_require_setup(self) -> None:
        """Verify locked state reports login instead of first setup."""
        client, _ = _make_client(StartupState.LOCKED)

        response = client.get("/auth/status")

        assert response.status_code == 200
        assert response.json()["startup_state"] == "locked"
        assert response.json()["needs_setup"] is False
        assert response.json()["authenticated"] is False

    def test_health_reports_not_ready(self) -> None:
        """Verify startup-gate health is explicit about not being ready."""
        client, _ = _make_client(StartupState.AUTO_UNLOCK_FAILED)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "auto_unlock_failed"
        assert response.json()["ready"] is False


class TestStartupGatePasswordSubmission:
    """Tests for setup and login password submission."""

    def test_setup_accepts_password_in_setup_state(self) -> None:
        """Verify setup state accepts the first password."""
        client, password_future = _make_client(StartupState.SETUP)

        response = client.post(
            "/auth/setup",
            json={"password": "valid-password"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "starting"}
        assert password_future.result() == "valid-password"

    def test_setup_rejects_locked_state(self) -> None:
        """Verify setup endpoint is rejected after an encrypted key exists."""
        client, password_future = _make_client(StartupState.LOCKED)

        response = client.post(
            "/auth/setup",
            json={"password": "valid-password"},
        )

        assert response.status_code == 409
        assert not password_future.done()

    def test_login_accepts_password_in_locked_state(self) -> None:
        """Verify locked state accepts the unlock password."""
        client, password_future = _make_client(StartupState.LOCKED)

        response = client.post(
            "/auth/login",
            json={"password": "valid-password"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "starting"}
        assert password_future.result() == "valid-password"

    def test_login_accepts_password_after_failed_autounlock(self) -> None:
        """Verify failed autounlock state accepts a manual password."""
        client, password_future = _make_client(StartupState.AUTO_UNLOCK_FAILED)

        response = client.post(
            "/auth/login",
            json={"password": "valid-password"},
        )

        assert response.status_code == 200
        assert password_future.result() == "valid-password"

    def test_login_rejects_setup_state(self) -> None:
        """Verify login endpoint is rejected before the encrypted key exists."""
        client, password_future = _make_client(StartupState.SETUP)

        response = client.post(
            "/auth/login",
            json={"password": "valid-password"},
        )

        assert response.status_code == 409
        assert not password_future.done()

    def test_short_password_is_rejected(self) -> None:
        """Verify short startup passwords are rejected."""
        client, password_future = _make_client(StartupState.SETUP)

        response = client.post("/auth/setup", json={"password": "short"})

        assert response.status_code == 422
        assert not password_future.done()
