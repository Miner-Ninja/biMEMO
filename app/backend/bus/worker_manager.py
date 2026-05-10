"""Worker process lifecycle management with health checking.

Spawns Tool Worker subprocesses using multiprocessing 'spawn' method,
passes shared secrets via multiprocessing.Pipe, monitors health via
process liveness, and restarts crashed workers.
"""

import asyncio
import contextlib
import logging
import multiprocessing
import os
import secrets
import tempfile
from typing import TYPE_CHECKING

import psutil

from backend.ipc.protocol import ToolRequest, ToolResponse
from backend.ipc.socket import WorkerSocketServer


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)

# --- Health check configuration ---
HEALTH_CHECK_INTERVAL = 5.0
MAX_RESTART_ATTEMPTS = 5
RSS_WARNING_MB = 500
RSS_RESTART_MB = 1024


def _tool_error_response(request_id: str, message: str) -> ToolResponse:
    """Build a standard tool error response."""
    return ToolResponse(
        request_id=request_id,
        status="error",
        error_message=message,
    )


class WorkerManager:
    """Manages a Tool Worker subprocess lifecycle.

    Handles spawning, secret passing, health checking, and
    automatic restart on crash.

    Attributes:
        worker_name: Identifier for this worker type.
        worker_target: Callable to run in the worker subprocess.
        socket_dir: Directory for Unix domain socket files.
    """

    def __init__(
        self,
        worker_name: str,
        worker_target: Callable,
        socket_dir: str | None = None,
    ) -> None:
        """Initialize the worker manager.

        Args:
            worker_name: Name identifier (e.g., "fs-worker").
            worker_target: Function to call in the child process.
            socket_dir: Directory for socket files (uses tempdir
                if not specified).
        """
        self.worker_name = worker_name
        self.worker_target = worker_target
        self.socket_dir = socket_dir or tempfile.mkdtemp(prefix="bimemo-")
        self._process: multiprocessing.Process | None = None
        self._secret: bytes = b""
        self._socket_server: WorkerSocketServer | None = None
        self._restart_count = 0
        self._running = False
        self._health_task: asyncio.Task | None = None
        self._handler: Callable = self._default_handler

    def set_handler(self, handler: Callable) -> None:
        """Set the request handler for incoming tool requests.

        Args:
            handler: Async function(ToolRequest) -> ToolResponse.
        """
        self._handler = handler

    @property
    def socket_path(self) -> str:
        """Return the Unix socket path for this worker."""
        return os.path.join(self.socket_dir, f"{self.worker_name}.sock")

    @property
    def is_alive(self) -> bool:
        """Check if the worker process is running."""
        return self._process is not None and self._process.is_alive()

    def _worker_pid(self) -> int | str:
        """Return the worker PID when available."""
        return self._process.pid if self._process else "?"

    async def _start_socket_server(self) -> None:
        """Start the worker socket server for this manager."""
        self._socket_server = WorkerSocketServer(
            self.socket_path,
            self._secret,
            self._handler,
        )
        await self._socket_server.start()

    async def _stop_socket_server(self) -> None:
        """Stop and clear the worker socket server when present."""
        if self._socket_server is None:
            return
        await self._socket_server.stop()
        self._socket_server = None

    async def _stop_health_task(self) -> None:
        """Cancel and clear the health-monitor task when present."""
        if self._health_task is None:
            return
        self._health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._health_task
        self._health_task = None

    async def start(self) -> None:
        """Spawn the worker and start the socket server."""
        self._running = True
        self._secret = secrets.token_bytes(32)
        await self._start_socket_server()
        self._spawn_worker()
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info(
            "Worker '%s' started (pid=%s)",
            self.worker_name,
            self._worker_pid(),
        )

    async def stop(self) -> None:
        """Stop the worker and clean up resources."""
        self._running = False
        await self._stop_health_task()
        self._stop_worker_process()
        self._process = None
        await self._stop_socket_server()
        logger.info("Worker '%s' stopped", self.worker_name)

    def _spawn_worker(self) -> None:
        """Spawn the worker subprocess using 'spawn' method.

        The secret is passed via multiprocessing.Pipe which is
        picklable and works across the spawn boundary. The child
        reads the secret from the connection and closes it.
        """
        parent_conn, child_conn = multiprocessing.Pipe()
        parent_conn.send_bytes(self._secret)
        parent_conn.close()

        ctx = multiprocessing.get_context("spawn")
        self._process = ctx.Process(
            target=self.worker_target,
            args=(self.socket_path, child_conn),
            name=self.worker_name,
            daemon=True,
        )
        self._process.start()
        child_conn.close()

        if self._socket_server is not None and self._process.pid:
            self._socket_server.set_expected_peer(self._process.pid, os.getuid())

    def _stop_worker_process(self) -> None:
        """Terminate the worker process and escalate to kill if needed."""
        if self._process is None or not self._process.is_alive():
            return
        self._process.terminate()
        self._process.join(timeout=5)
        if self._process.is_alive():
            self._process.kill()

    def _check_rss(self) -> float | None:
        """Return worker RSS in MB, or None if unavailable."""
        if self._process is None or self._process.pid is None:
            return None
        try:
            proc = psutil.Process(self._process.pid)
            return proc.memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    async def _health_loop(self) -> None:
        """Periodically check worker health and restart if needed."""
        while self._running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            if not self._running:
                break
            if not self.is_alive:
                logger.warning(
                    "Worker '%s' died, restarting (attempt %d/%d)",
                    self.worker_name,
                    self._restart_count + 1,
                    MAX_RESTART_ATTEMPTS,
                )
                await self._restart()
                continue

            rss_mb = self._check_rss()
            if rss_mb is not None:
                logger.debug(
                    "Worker '%s' RSS: %.1f MB",
                    self.worker_name,
                    rss_mb,
                )
                if rss_mb > RSS_RESTART_MB:
                    logger.warning(
                        "Worker '%s' RSS %.1f MB exceeds %d MB limit, restarting",
                        self.worker_name,
                        rss_mb,
                        RSS_RESTART_MB,
                    )
                    await self._restart()
                elif rss_mb > RSS_WARNING_MB:
                    logger.warning(
                        "Worker '%s' RSS %.1f MB exceeds %d MB warning threshold",
                        self.worker_name,
                        rss_mb,
                        RSS_WARNING_MB,
                    )

    async def _restart(self) -> None:
        """Restart the worker subprocess."""
        if self._restart_count >= MAX_RESTART_ATTEMPTS:
            logger.error(
                "Worker '%s' exceeded max restart attempts",
                self.worker_name,
            )
            self._running = False
            return

        self._restart_count += 1
        self._secret = secrets.token_bytes(32)

        if self._socket_server is not None:
            self._socket_server.secret = self._secret

        self._spawn_worker()
        logger.info(
            "Worker '%s' restarted (pid=%s)",
            self.worker_name,
            self._worker_pid(),
        )

    async def send_request(self, request: ToolRequest) -> ToolResponse:
        """Send a tool request to this worker.

        Args:
            request: Tool execution request.

        Returns:
            Tool execution response.
        """
        if self._socket_server is None:
            return _tool_error_response(request.request_id, "Worker not started")
        self._socket_server.track_request(request.request_id)
        return await self._handler(request)

    @staticmethod
    async def _default_handler(
        request: ToolRequest,
    ) -> ToolResponse:
        """Default handler that returns an error.

        Args:
            request: Tool execution request.

        Returns:
            Error response indicating no handler is configured.
        """
        return _tool_error_response(request.request_id, "No handler configured")
