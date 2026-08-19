"""Integration tests for password-first supervisor startup lifecycle."""

from dataclasses import dataclass

import pytest

from backend.ipc.supervisor import SupervisorError
from backend.processes import supervisor as supervisor_module
from backend.security.startup_state import StartupState


MANUAL_PASSWORD = "manual-startup-password"


@dataclass
class FakeSupervisor:
    """Minimal supervisor stand-in for startup lifecycle tests.

    Attributes:
        should_fail: Whether start should raise a SupervisorError.
        started: Whether start was called successfully.
        shutdown_called: Whether shutdown was called.
    """

    should_fail: bool = False
    started: bool = False
    shutdown_called: bool = False

    async def start(self) -> None:
        """Start or fail according to test configuration."""
        if self.should_fail:
            raise SupervisorError("simulated startup failure")
        self.started = True

    async def shutdown(self) -> None:
        """Record shutdown calls."""
        self.shutdown_called = True


class TestSupervisorStartupLifecycle:
    """Tests for mapping startup states to gate and supervisor behavior."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("startup_state", "expected_password"),
        [
            (StartupState.SETUP, MANUAL_PASSWORD),
            (StartupState.LOCKED, MANUAL_PASSWORD),
            (StartupState.AUTO_UNLOCK, None),
            (StartupState.READY, None),
        ],
    )
    async def test_startup_state_builds_expected_supervisor(
        self,
        monkeypatch,
        startup_state: StartupState,
        expected_password: str | None,
    ) -> None:
        """Verify each startup state either waits for manual password or starts."""
        built_passwords: list[str | None] = []
        supervisors: list[FakeSupervisor] = []
        gate_states: list[StartupState] = []

        async def fake_gate(settings: object, state: object) -> str | None:
            """Return a manual password only for gate-backed states."""
            del settings
            if state not in {
                StartupState.SETUP,
                StartupState.LOCKED,
                StartupState.AUTO_UNLOCK_FAILED,
            }:
                return None
            gate_states.append(state)
            return MANUAL_PASSWORD

        def fake_build_supervisor(password: str | None = None) -> FakeSupervisor:
            """Build one fake supervisor and record the password it received."""
            built_passwords.append(password)
            supervisor = FakeSupervisor()
            supervisors.append(supervisor)
            return supervisor

        monkeypatch.setattr(
            supervisor_module,
            "_startup_password_from_gate",
            fake_gate,
        )
        monkeypatch.setattr(
            supervisor_module,
            "_build_supervisor",
            fake_build_supervisor,
        )

        result = await supervisor_module._start_supervisor_with_password_retry(
            settings=object(),
            startup_state=startup_state,
        )

        assert result is supervisors[0]
        assert supervisors[0].started is True
        assert built_passwords == [expected_password]
        if expected_password is None:
            assert gate_states == []
        else:
            assert gate_states == [startup_state]

    @pytest.mark.asyncio
    async def test_failed_autounlock_falls_back_to_manual_gate(
        self,
        monkeypatch,
    ) -> None:
        """Verify failed autounlock retries through the manual unlock gate."""
        built_passwords: list[str | None] = []
        gate_states: list[StartupState] = []
        supervisors = [
            FakeSupervisor(should_fail=True),
            FakeSupervisor(),
        ]

        async def fake_gate(settings: object, state: object) -> str | None:
            """Return a manual password after failed autounlock."""
            del settings
            if state != StartupState.AUTO_UNLOCK_FAILED:
                return None
            gate_states.append(state)
            return MANUAL_PASSWORD

        def fake_build_supervisor(password: str | None = None) -> FakeSupervisor:
            """Return fake supervisors in startup-attempt order."""
            built_passwords.append(password)
            return supervisors[len(built_passwords) - 1]

        monkeypatch.setattr(
            supervisor_module,
            "_startup_password_from_gate",
            fake_gate,
        )
        monkeypatch.setattr(
            supervisor_module,
            "_build_supervisor",
            fake_build_supervisor,
        )

        result = await supervisor_module._start_supervisor_with_password_retry(
            settings=object(),
            startup_state=StartupState.AUTO_UNLOCK,
        )

        assert result is supervisors[1]
        assert supervisors[0].shutdown_called is True
        assert supervisors[1].started is True
        assert built_passwords == [None, MANUAL_PASSWORD]
        assert gate_states == [StartupState.AUTO_UNLOCK_FAILED]
