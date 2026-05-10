"""High-level worker isolation manager.

Wraps the socket server/client with worker lifecycle management,
health checking, and graceful shutdown.
"""

# ruff: noqa: ASYNC109

import asyncio
import logging
import multiprocessing
import multiprocessing.connection
import os
import secrets
import signal
import sys
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.ipc.ipc_channel import (
    IPCChannelError as IPCChannelError,
)
from backend.ipc.ipc_channel import (
    IPCSocketClient as IPCSocketClient,
)
from backend.ipc.ipc_channel import (
    IPCSocketServer as IPCSocketServer,
)
from backend.ipc.models import (
    ChatRequest as ChatRequest,
)
from backend.ipc.models import (
    ChatResponse as ChatResponse,
)
from backend.ipc.models import (
    ConfirmRequest as ConfirmRequest,
)
from backend.ipc.models import (
    ConfirmResponse as ConfirmResponse,
)
from backend.ipc.models import (
    DBOperation as DBOperation,
)
from backend.ipc.models import (
    DBResult as DBResult,
)
from backend.ipc.models import (
    StopRequest as StopRequest,
)
from backend.ipc.models import (
    StopResponse as StopResponse,
)
from backend.ipc.models import (
    StreamEvent as StreamEvent,
)
from backend.ipc.models import (
    StreamEventType as StreamEventType,
)
from backend.ipc.protocol import ToolRequest, ToolResponse
from backend.ipc.socket import Handler, WorkerSocketClient, WorkerSocketServer


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType


logger = logging.getLogger(__name__)


HEALTH_CHECK_INTERVAL = 5.0
RSS_WARNING_MB = 500
RSS_RESTART_MB = 1024
MAX_RESTART_ATTEMPTS = 5
DEFAULT_TOOL_TIMEOUT = 30.0


@dataclass
class WorkerConfig:
    """Configuration for a tool worker."""

    name: str
    handler: Callable[..., Any]
    allowed_dirs: list[str] | None = None
    allowed_hosts: list[str] | None = None
    timeout: float = DEFAULT_TOOL_TIMEOUT


@dataclass
class WorkerStatus:
    """Runtime status of a worker."""

    name: str
    is_alive: bool = False
    restarts: int = 0
    last_error: str | None = None


class WorkerIsolator:
    """Manages isolated tool workers with IPC communication.

    Provides:
    - Process spawning and lifecycle management
    - Unix socket IPC with HMAC authentication
    - Health monitoring with automatic restart
    - Graceful shutdown

    Example:
        async def my_handler(request: ToolRequest) -> ToolResponse:
            # Process tool request
            return ToolResponse(
                request_id=request.request_id,
                status="success",
                result={},
            )

        isolator = WorkerIsolator("fs-worker", my_handler)
        await isolator.start()

        # Use client to send requests
        response = await isolator.client.send_request(
            ToolRequest(
                request_id="1",
                tool_name="file_read",
                parameters={"path": "/tmp/test"},
            )
        )
    """

    def __init__(
        self,
        name: str,
        handler: Handler,
        socket_dir: str | None = None,
    ) -> None:
        """Initialize the isolator.

        Args:
            name: Worker name for logging.
            handler: Async function to handle tool requests.
            socket_dir: Directory for Unix socket. Defaults to temp dir.
        """
        self.name = name
        self.handler = handler
        self.socket_dir = socket_dir or tempfile.mkdtemp(prefix="llm_guard-")

        self._secret: bytes = b""
        self._process: multiprocessing.Process | None = None
        self._socket_server: WorkerSocketServer | None = None
        self._client: WorkerSocketClient | None = None
        self._running = False
        self._restart_count = 0

        self.status = WorkerStatus(name=name)

    async def start(self) -> None:
        """Start the worker isolator.

        Generates a secret, starts the socket server, spawns the worker process,
        and connects the client.
        """
        self._secret = secrets.token_bytes(32)
        socket_path = os.path.join(self.socket_dir, f"{self.name}.sock")

        self._socket_server = WorkerSocketServer(
            socket_path=socket_path,
            secret=self._secret,
            handler=self.handler,
        )
        await self._socket_server.start()

        self._spawn_worker(socket_path)

        self._client = WorkerSocketClient(socket_path, self._secret)
        await self._client.connect()

        self._running = True

    def _spawn_worker(self, socket_path: str) -> None:
        """Spawn the worker process."""
        parent_conn, child_conn = multiprocessing.Pipe()
        parent_conn.send_bytes(self._secret)
        parent_conn.close()

        ctx = multiprocessing.get_context("spawn")
        self._process = ctx.Process(
            target=self._worker_main,
            args=(socket_path, child_conn),
            name=self.name,
            daemon=True,
        )
        self._process.start()
        child_conn.close()

        if self._socket_server:
            self._socket_server.set_expected_peer(self._process.pid, os.getuid())

        logger.info("Worker '%s' spawned with pid %d", self.name, self._process.pid)

    def _worker_main(
        self,
        socket_path: str,
        conn: multiprocessing.connection.Connection,
    ) -> None:
        """Worker process entry point.

        Receives secret via pipe, then blocks until signal.
        """
        conn.recv_bytes()
        conn.close()

        def shutdown(
            signum: int,
            frame: FrameType | None,
        ) -> None:
            logger.info("Worker '%s' received shutdown signal", self.name)
            if os.path.exists(socket_path):
                os.unlink(socket_path)
            sys.exit(0)

        signal.signal(signal.SIGTERM, shutdown)
        signal.pause()

    async def stop(self) -> None:
        """Stop the worker isolator gracefully.

        Sends SIGTERM to worker, closes client and server.
        """
        self._running = False

        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()

        if self._client:
            await self._client.close()
            self._client = None

        if self._socket_server:
            await self._socket_server.stop()
            self._socket_server = None

        self.status.is_alive = False
        logger.info("Worker '%s' stopped", self.name)

    @property
    def client(self) -> WorkerSocketClient:
        """Return the IPC client for sending requests to the worker."""
        if self._client is None:
            raise RuntimeError("Isolator not started")
        return self._client

    def is_alive(self) -> bool:
        """Check if the worker process is alive."""
        return self._process is not None and self._process.is_alive()

    async def send_request(
        self,
        request: ToolRequest,
        timeout: float | None = None,
    ) -> ToolResponse:
        """Send a request to the worker with optional timeout."""
        if not self.is_alive():
            return ToolResponse(
                request_id=request.request_id,
                status="error",
                error_message=f"Worker '{self.name}' is not alive",
            )

        timeout = timeout or DEFAULT_TOOL_TIMEOUT
        try:
            if self._socket_server:
                self._socket_server.track_request(request.request_id)
            return await asyncio.wait_for(
                self._client.send_request(request),
                timeout=timeout,
            )
        except TimeoutError:
            self.status.last_error = f"Timeout for {request.tool_name}"
            logger.error("Worker '%s' timeout for %s", self.name, request.tool_name)
            return ToolResponse(
                request_id=request.request_id,
                status="error",
                error_message=f"Worker timeout for {request.tool_name}",
            )
