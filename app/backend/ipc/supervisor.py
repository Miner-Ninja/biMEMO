"""Generic process supervisor for multi-process LLM application architecture.

Spawns child processes in dependency order using multiprocessing.spawn,
distributes per-channel HMAC secrets via Pipe, gates startup on health
checks, monitors for crashes with auto-restart, and coordinates graceful
shutdown in reverse startup order. Designed to be the container entrypoint
for applications that split their monolithic process into isolated components.
"""

import asyncio
import contextlib
import logging
import multiprocessing
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


logger = logging.getLogger(__name__)

# --- Constants ---
DEFAULT_HEALTH_TIMEOUT = 10.0
DEFAULT_SHUTDOWN_TIMEOUT = 5.0
DEFAULT_MAX_RESTARTS = 5
HEALTH_CHECK_INTERVAL = 2.0
MONITOR_INTERVAL = 1.0


@dataclass
class ProcessSpec:
    """Specification for a child process to be supervised.

    Attributes:
        name: Human-readable process name for logging.
        target: Callable to run in the child process.
        args_factory: Callable that returns args tuple for the target.
            Receives a dict of channel secrets keyed by channel name.
        health_check: Optional async callable that returns True when
            the process is ready. Called repeatedly until success or timeout.
        depends_on: List of process names that must start before this one.
        max_restarts: Maximum consecutive restart attempts.
        shutdown_timeout: Seconds to wait for graceful shutdown before SIGKILL.
    """

    name: str
    target: Callable[..., None]
    args_factory: Callable[[dict[str, bytes]], tuple[Any, ...]]
    health_check: Callable[[], Coroutine[Any, Any, bool]] | None = None
    depends_on: list[str] = field(default_factory=list)
    max_restarts: int = DEFAULT_MAX_RESTARTS
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT


@dataclass
class ChildProcess:
    """Runtime state of a supervised child process.

    Attributes:
        spec: The process specification.
        process: The multiprocessing.Process instance.
        restart_count: Number of consecutive restarts.
        started_at: Timestamp when the process was last started.
    """

    spec: ProcessSpec
    process: multiprocessing.Process | None = None
    restart_count: int = 0
    started_at: float = 0.0


class SupervisorError(Exception):
    """Raised when the supervisor encounters a fatal error."""


class ProcessSupervisor:
    """Manages child processes with dependency-ordered startup and crash recovery.

    The caller is responsible for generating secrets and passing them
    via set_secrets() before calling start(). The supervisor stores
    and distributes secrets to processes via their args_factory, but
    does not generate or rotate them.

    Example:
        supervisor = ProcessSupervisor()
        supervisor.add_process(ProcessSpec(
            name="db-proxy",
            target=db_proxy_main,
            args_factory=lambda secrets: (secrets["db_proxy"],),
            health_check=check_db_proxy_health,
        ))
        supervisor.add_process(ProcessSpec(
            name="orchestrator",
            target=orchestrator_main,
            args_factory=lambda secrets: (secrets["orchestrator"],),
            depends_on=["db-proxy"],
        ))
        supervisor.set_secrets({
            "db_proxy": os.urandom(32),
            "orchestrator": os.urandom(32),
        })
        await supervisor.start()
    """

    def __init__(self) -> None:
        """Initialize the supervisor with no processes."""
        self._specs: list[ProcessSpec] = []
        self._children: dict[str, ChildProcess] = {}
        self._secrets: dict[str, bytes] = {}
        self._running = False
        self._monitor_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

    def add_process(self, spec: ProcessSpec) -> None:
        """Add a process specification to be supervised.

        Args:
            spec: Process specification with target, dependencies, etc.
        """
        self._specs.append(spec)
        logger.debug("Added process spec: %s", spec.name)

    def set_secrets(self, secrets: dict[str, bytes]) -> None:
        """Set channel secrets for process argument factories.

        Must be called before start(). The supervisor stores these
        secrets and passes them to each process via its args_factory.

        Args:
            secrets: Mapping of channel name to 32-byte HMAC secret.
        """
        self._secrets = dict(secrets)
        logger.debug("Received %d channel secrets", len(self._secrets))

    async def start(self) -> None:
        """Start all processes in dependency order.

        Spawns each process after its dependencies are healthy.
        Passes stored secrets to each process via its args_factory.

        Raises:
            SupervisorError: If secrets not set or process fails to start.
        """
        if not self._secrets:
            raise SupervisorError("No secrets set. Call set_secrets() before start().")
        self._running = True

        start_order = self._resolve_start_order()
        logger.info(
            "Starting processes in order: %s",
            [s.name for s in start_order],
        )

        for spec in start_order:
            await self._start_process(spec)

        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("All processes started, monitoring active")

    async def shutdown(self) -> None:
        """Shut down all processes in reverse startup order.

        Sends SIGTERM to each process, waits for shutdown_timeout,
        then sends SIGKILL if still alive.
        """
        self._running = False
        self._shutdown_event.set()

        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

        shutdown_order = list(reversed(self._resolve_start_order()))
        logger.info(
            "Shutting down processes in order: %s",
            [s.name for s in shutdown_order],
        )

        for spec in shutdown_order:
            child = self._children.get(spec.name)
            if child is None or child.process is None:
                continue
            await self._stop_process(child)

        logger.info("All processes stopped")

    def is_running(self) -> bool:
        """Check if the supervisor is actively monitoring."""
        return self._running

    @staticmethod
    def _child_pid(child: ChildProcess) -> int | str:
        """Return one child PID or a placeholder when unavailable."""
        process = child.process
        if process is None or process.pid is None:
            return "?"
        return process.pid

    @staticmethod
    def _is_process_alive(child: ChildProcess) -> bool:
        """Return whether one child currently has a live process."""
        return child.process is not None and child.process.is_alive()

    @staticmethod
    def _health_check_deadline(timeout_seconds: float) -> float:
        """Return the monotonic deadline for one health-check wait."""
        return time.monotonic() + timeout_seconds

    @staticmethod
    def _health_check_timeout_error(
        process_name: str,
        timeout_seconds: float,
    ) -> SupervisorError:
        """Build a standard timeout error for one child health check."""
        return SupervisorError(
            f"Health check timeout for process '{process_name}' "
            f"after {timeout_seconds:.0f}s"
        )

    @staticmethod
    async def _terminate_process(
        process: multiprocessing.Process,
        *,
        timeout_seconds: float,
    ) -> bool:
        """Terminate one child process and return whether kill was needed."""
        process.terminate()
        process.join(timeout=timeout_seconds)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
            return True
        return False

    def get_child_pids(self) -> dict[str, int | None]:
        """Return a mapping of process names to their PIDs.

        Returns:
            Dict of name → PID (None if not running).
        """
        return {
            name: (child.process.pid if self._is_process_alive(child) else None)
            for name, child in self._children.items()
        }

    def _resolve_start_order(self) -> list[ProcessSpec]:
        """Topologically sort process specs by dependencies.

        Returns:
            List of ProcessSpec in dependency-satisfying order.

        Raises:
            SupervisorError: If dependencies cycle or reference unknown processes.
        """
        specs_by_name = {
            process_spec.name: process_spec for process_spec in self._specs
        }
        visited: set[str] = set()
        order: list[ProcessSpec] = []
        visiting: set[str] = set()

        def visit(name: str) -> None:
            """Depth-first visit for topological sort."""
            if name in visited:
                return
            if name in visiting:
                raise SupervisorError(
                    f"Circular dependency detected involving '{name}'"
                )
            if name not in specs_by_name:
                raise SupervisorError(f"Unknown dependency: '{name}'")
            visiting.add(name)
            for dep in specs_by_name[name].depends_on:
                visit(dep)
            visiting.remove(name)
            visited.add(name)
            order.append(specs_by_name[name])

        for spec in self._specs:
            visit(spec.name)

        return order

    async def _start_process(self, spec: ProcessSpec) -> None:
        """Spawn a single child process and wait for its health check.

        Args:
            spec: Process specification.

        Raises:
            SupervisorError: If health check times out.
        """
        child = ChildProcess(spec=spec)
        self._spawn(child)
        self._children[spec.name] = child

        if spec.health_check is not None:
            await self._wait_for_health(spec)

        logger.info(
            "Process '%s' started (pid=%s)",
            spec.name,
            self._child_pid(child),
        )

    def _spawn(self, child: ChildProcess) -> None:
        """Spawn the child process using multiprocessing.spawn.

        Args:
            child: ChildProcess to spawn.
        """
        args = child.spec.args_factory(self._secrets)

        ctx = multiprocessing.get_context("spawn")
        child.process = ctx.Process(
            target=child.spec.target,
            args=args,
            name=child.spec.name,
            daemon=True,
        )
        child.process.start()
        child.started_at = time.monotonic()

    async def _wait_for_health(
        self,
        spec: ProcessSpec,
        timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT,
    ) -> None:
        """Poll the health check until success or timeout.

        Args:
            spec: Process specification with health_check callable.
            timeout_seconds: Maximum seconds to wait.

        Raises:
            SupervisorError: If health check does not pass within timeout.
        """
        deadline = self._health_check_deadline(timeout_seconds)
        while time.monotonic() < deadline:
            if await self._health_check_passed(spec):
                return
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

        raise self._health_check_timeout_error(spec.name, timeout_seconds)

    async def _health_check_passed(self, spec: ProcessSpec) -> bool:
        """Return whether one child currently passes its health check."""
        health_check = spec.health_check
        if health_check is None:
            return True
        try:
            return await health_check()
        except Exception as e:
            logger.debug(
                "Health check for '%s' not ready: %s",
                spec.name,
                e,
            )
            return False

    async def _stop_process(self, child: ChildProcess) -> None:
        """Stop a child process gracefully, escalating to SIGKILL.

        Args:
            child: ChildProcess to stop.
        """
        if not self._is_process_alive(child):
            return
        proc = child.process
        if proc is None:
            return

        logger.info("Stopping process '%s' (pid=%d)", child.spec.name, proc.pid)
        needed_kill = await self._terminate_process(
            proc,
            timeout_seconds=child.spec.shutdown_timeout,
        )
        if needed_kill:
            logger.warning(
                "Process '%s' did not exit in %.0fs, sending SIGKILL",
                child.spec.name,
                child.spec.shutdown_timeout,
            )

    async def _monitor_loop(self) -> None:
        """Periodically check child processes and restart crashed ones."""
        while self._running:
            if await self._wait_for_shutdown_signal():
                break

            for name, child in self._children.items():
                if not self._running:
                    break
                if not self._should_handle_crashed_child(child):
                    continue

                logger.warning(
                    "Process '%s' (pid=%s) exited unexpectedly",
                    name,
                    self._child_pid(child),
                )
                await self._handle_crash(child)

    async def _wait_for_shutdown_signal(self) -> bool:
        """Wait one monitor interval and report whether shutdown was requested."""
        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=MONITOR_INTERVAL,
            )
            return True
        except TimeoutError:
            return False

    def _should_handle_crashed_child(self, child: ChildProcess) -> bool:
        """Return whether one child needs crash handling on this monitor pass."""
        return child.process is not None and not child.process.is_alive()

    async def _shutdown_after_failed_restart(self, child: ChildProcess) -> None:
        """Stop the supervisor after one crashed child exceeds restart limits."""
        logger.error(
            "Process '%s' exceeded max restarts (%d), shutting down",
            child.spec.name,
            child.spec.max_restarts,
        )
        self._running = False
        await self.shutdown()

    async def _restart_child(self, child: ChildProcess) -> None:
        """Restart one crashed child and verify its health if configured."""
        logger.info(
            "Restarting process '%s' (attempt %d/%d)",
            child.spec.name,
            child.restart_count,
            child.spec.max_restarts,
        )
        self._spawn(child)
        if child.spec.health_check is None:
            return
        try:
            await self._wait_for_health(child.spec)
        except SupervisorError:
            logger.error(
                "Restarted process '%s' failed health check",
                child.spec.name,
            )
            self._running = False
            await self.shutdown()

    async def _handle_crash(self, child: ChildProcess) -> None:
        """Handle a crashed child process with restart or fatal exit.

        Args:
            child: The crashed ChildProcess.
        """
        child.restart_count += 1

        if child.restart_count > child.spec.max_restarts:
            await self._shutdown_after_failed_restart(child)
            return

        await self._restart_child(child)
