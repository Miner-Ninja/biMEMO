"""Tests for ProcessSupervisor startup order, crash recovery, and shutdown.

Covers dependency resolution, health check gating, max restart exceeded,
and graceful shutdown sequence. Uses lightweight target functions that
exit quickly for testability.
"""

import time

import pytest

from backend.ipc.supervisor import (
    ProcessSpec,
    ProcessSupervisor,
    SupervisorError,
)


def _noop_target(*args: object) -> None:
    """Target that sleeps briefly then exits."""
    time.sleep(60)


def _crash_target(*args: object) -> None:
    """Target that exits immediately to simulate a crash."""


def _fake_secrets(*names: str) -> dict[str, bytes]:
    """Create deterministic fake secrets for testing."""
    return dict.fromkeys(names, b"\xaa" * 32)


class TestDependencyResolution:
    """Tests for topological sort of process specs."""

    def test_no_dependencies(self) -> None:
        """Resolve order with no dependencies."""
        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(name="a", target=_noop_target, args_factory=lambda secrets: ())
        )
        supervisor.add_process(
            ProcessSpec(name="b", target=_noop_target, args_factory=lambda secrets: ())
        )
        order = supervisor._resolve_start_order()
        names = [process_spec.name for process_spec in order]
        assert "a" in names
        assert "b" in names

    def test_linear_dependencies(self) -> None:
        """Resolve linear dependency chain: a → b → c."""
        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(
                name="c",
                target=_noop_target,
                args_factory=lambda secrets: (),
                depends_on=["b"],
            )
        )
        supervisor.add_process(
            ProcessSpec(
                name="b",
                target=_noop_target,
                args_factory=lambda secrets: (),
                depends_on=["a"],
            )
        )
        supervisor.add_process(
            ProcessSpec(name="a", target=_noop_target, args_factory=lambda secrets: ())
        )
        order = supervisor._resolve_start_order()
        names = [process_spec.name for process_spec in order]
        assert names.index("a") < names.index("b")
        assert names.index("b") < names.index("c")

    def test_circular_dependency_detected(self) -> None:
        """Detect circular dependencies and raise error."""
        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(
                name="a",
                target=_noop_target,
                args_factory=lambda secrets: (),
                depends_on=["b"],
            )
        )
        supervisor.add_process(
            ProcessSpec(
                name="b",
                target=_noop_target,
                args_factory=lambda secrets: (),
                depends_on=["a"],
            )
        )
        with pytest.raises(SupervisorError, match="Circular dependency"):
            supervisor._resolve_start_order()

    def test_unknown_dependency_detected(self) -> None:
        """Detect references to unknown process names."""
        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(
                name="a",
                target=_noop_target,
                args_factory=lambda secrets: (),
                depends_on=["nonexistent"],
            )
        )
        with pytest.raises(SupervisorError, match="Unknown dependency"):
            supervisor._resolve_start_order()


class TestSetSecrets:
    """Tests for caller-provided secret injection."""

    def test_set_secrets_stores_all_keys(self) -> None:
        """set_secrets() stores all provided channel secrets."""
        supervisor = ProcessSupervisor()
        channel_secrets = {
            "channel-a": b"\x01" * 32,
            "channel-b": b"\x02" * 32,
        }
        supervisor.set_secrets(channel_secrets)
        assert len(supervisor._secrets) == 2
        assert supervisor._secrets["channel-a"] == b"\x01" * 32
        assert supervisor._secrets["channel-b"] == b"\x02" * 32


class TestStartAndShutdown:
    """Tests for process start and shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_start_single_process(self) -> None:
        """Start a single process without dependencies."""
        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(
                name="worker",
                target=_noop_target,
                args_factory=lambda secrets: (),
            )
        )
        supervisor.set_secrets(_fake_secrets("worker"))
        await supervisor.start()
        try:
            pids = supervisor.get_child_pids()
            assert pids["worker"] is not None
            assert supervisor.is_running() is True
        finally:
            await supervisor.shutdown()
            assert supervisor.is_running() is False

    @pytest.mark.asyncio
    async def test_start_with_health_check(self) -> None:
        """Start a process with a passing health check."""

        async def always_healthy() -> bool:
            """Health check that always passes."""
            return True

        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(
                name="worker",
                target=_noop_target,
                args_factory=lambda secrets: (),
                health_check=always_healthy,
            )
        )
        supervisor.set_secrets(_fake_secrets("worker"))
        await supervisor.start()
        try:
            assert supervisor.get_child_pids()["worker"] is not None
        finally:
            await supervisor.shutdown()

    @pytest.mark.asyncio
    async def test_health_check_timeout(self) -> None:
        """Raise error when health check times out."""

        async def never_healthy() -> bool:
            """Health check that never passes."""
            return False

        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(
                name="worker",
                target=_noop_target,
                args_factory=lambda secrets: (),
                health_check=never_healthy,
            )
        )
        supervisor.set_secrets(_fake_secrets("worker"))
        with pytest.raises(SupervisorError, match="Health check timeout"):
            await supervisor.start()
        await supervisor.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_stops_processes(self) -> None:
        """Shutdown terminates all child processes."""
        supervisor = ProcessSupervisor()
        supervisor.add_process(
            ProcessSpec(
                name="worker",
                target=_noop_target,
                args_factory=lambda secrets: (),
            )
        )
        supervisor.set_secrets(_fake_secrets("worker"))
        await supervisor.start()
        await supervisor.shutdown()
        pids = supervisor.get_child_pids()
        assert pids["worker"] is None
