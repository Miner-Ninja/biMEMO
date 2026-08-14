"""Unix domain socket server/client for Core-Worker IPC.

Provides length-prefixed, HMAC-authenticated JSON communication
over Unix domain sockets.
"""

import asyncio
import logging
import os
import socket
import struct
from collections.abc import Callable, Coroutine

from backend.ipc.protocol import (
    LENGTH_PREFIX_LEN,
    MAX_MESSAGE_SIZE,
    ToolRequest,
    ToolResponse,
    sign_message,
    verify_and_extract,
)


logger = logging.getLogger(__name__)


class WorkerSocketError(Exception):
    """Raised when socket communication fails."""


Handler = Callable[[ToolRequest], Coroutine[None, None, ToolResponse]]


def _error_response(request_id: str, message: str) -> ToolResponse:
    """Build a standard error response for socket-level failures."""
    return ToolResponse(
        request_id=request_id,
        status="error",
        error_message=message,
    )


def _signed_model_frame(model: ToolRequest | ToolResponse, secret: bytes) -> bytes:
    """Serialize one socket model and wrap it in a signed frame."""
    body = model.model_dump_json().encode()
    return sign_message(body, secret)


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Close a stream writer and wait for shutdown."""
    writer.close()
    await writer.wait_closed()


async def _read_signed_payload(
    reader: asyncio.StreamReader,
    secret: bytes,
    *,
    oversize_error: str,
) -> bytes:
    """Read one signed length-prefixed payload from the socket."""
    length_data = await reader.readexactly(LENGTH_PREFIX_LEN)
    (payload_len,) = struct.unpack(">I", length_data)
    if payload_len > MAX_MESSAGE_SIZE:
        raise WorkerSocketError(oversize_error)
    payload = await reader.readexactly(payload_len)
    return verify_and_extract(payload, secret)


class WorkerSocketServer:
    """Unix domain socket server for receiving worker connections.

    Attributes:
        socket_path: Filesystem path for the Unix socket.
        secret: HMAC shared secret for this worker.
        handler: Async callable to process tool requests.
    """

    def __init__(
        self,
        socket_path: str,
        secret: bytes,
        handler: Handler,
    ) -> None:
        """Initialize the server.

        Args:
            socket_path: Path for the Unix domain socket file.
            secret: 32-byte HMAC shared secret.
            handler: Async function(ToolRequest) -> ToolResponse.
        """
        self.socket_path = socket_path
        self.secret = secret
        self.handler = handler
        self._server: asyncio.Server | None = None
        self._expected_pid: int | None = None
        self._expected_uid: int | None = None
        self._pending_requests: set[str] = set()

    def set_expected_peer(self, pid: int, uid: int) -> None:
        """Set the expected peer credentials for SO_PEERCRED.

        Args:
            pid: Expected worker process ID.
            uid: Expected worker user ID.
        """
        self._expected_pid = pid
        self._expected_uid = uid

    def track_request(self, request_id: str) -> None:
        """Register a request ID as pending.

        Args:
            request_id: Request ID issued to this worker.
        """
        self._pending_requests.add(request_id)

    def start_sync(self) -> None:
        """Remove stale socket file before async start."""
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    async def start(self) -> None:
        """Start listening on the Unix domain socket."""
        self.start_sync()

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self.socket_path,
        )
        os.chmod(self.socket_path, 0o600)
        logger.info(
            "Worker socket server started at %s",
            self.socket_path,
        )

    def stop_sync(self) -> None:
        """Remove socket file during cleanup."""
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    async def stop(self) -> None:
        """Stop the server and clean up the socket file."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.stop_sync()

    def _verify_peer(self, writer: asyncio.StreamWriter) -> bool:
        """Verify connecting peer via SO_PEERCRED."""
        if self._expected_pid is None:
            return True

        sock = writer.get_extra_info("socket")
        if sock is None:
            return False

        cred = sock.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("iII"),
        )
        pid, uid, _ = struct.unpack("iII", cred)

        if pid != self._expected_pid or uid != self._expected_uid:
            logger.warning(
                "Rejected connection: expected pid=%d uid=%d, got pid=%d uid=%d",
                self._expected_pid,
                self._expected_uid,
                pid,
                uid,
            )
            return False
        return True

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming worker connection."""
        if not self._verify_peer(writer):
            await _close_writer(writer)
            return

        try:
            while True:
                response = await self._read_and_process(reader)
                if response is None:
                    break
                await self._send_response(writer, response)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            logger.debug("Worker disconnected")
        finally:
            await _close_writer(writer)

    async def _read_and_process(
        self, reader: asyncio.StreamReader
    ) -> ToolResponse | None:
        """Read one request, process it, return a response."""
        try:
            message_bytes = await _read_signed_payload(
                reader,
                self.secret,
                oversize_error="Message exceeds size limit",
            )
        except WorkerSocketError:
            logger.warning("Oversized message rejected")
            return _error_response("", "Message exceeds size limit")
        request = ToolRequest.model_validate_json(message_bytes)

        if request.request_id not in self._pending_requests:
            logger.warning("Unknown request_id: %s", request.request_id)
            return _error_response(request.request_id, "Unknown request_id")

        self._pending_requests.discard(request.request_id)
        return await self.handler(request)

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        response: ToolResponse,
    ) -> None:
        """Send a signed response to the worker."""
        frame = _signed_model_frame(response, self.secret)
        writer.write(frame)
        await writer.drain()


class WorkerSocketClient:
    """Unix domain socket client for Tool Worker processes.

    Attributes:
        socket_path: Path to the Core's Unix domain socket.
        secret: HMAC shared secret for authentication.
    """

    def __init__(self, socket_path: str, secret: bytes) -> None:
        """Initialize the client.

        Args:
            socket_path: Path to the Unix domain socket.
            secret: 32-byte HMAC shared secret.
        """
        self.socket_path = socket_path
        self.secret = secret
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        """Connect to the Core socket server."""
        self._reader, self._writer = await asyncio.open_unix_connection(
            self.socket_path
        )

    async def close(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            await _close_writer(self._writer)
            self._writer = None
            self._reader = None

    async def send_request(self, request: ToolRequest) -> ToolResponse:
        """Send a tool request and wait for the response."""
        if self._writer is None or self._reader is None:
            raise WorkerSocketError("Not connected")

        frame = _signed_model_frame(request, self.secret)
        self._writer.write(frame)
        await self._writer.drain()

        response_bytes = await _read_signed_payload(
            self._reader,
            self.secret,
            oversize_error="Response exceeds size limit",
        )
        return ToolResponse.model_validate_json(response_bytes)
