"""Unix domain socket server/client for Core-Worker IPC.

Provides length-prefixed, HMAC-authenticated JSON communication
over Unix domain sockets. The server runs in Core, clients run
in Tool Worker subprocesses. Socket permissions are set to 0600.
"""

import asyncio
import logging
import os
import socket
import struct

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


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Close one stream writer and wait until it finishes closing."""
    writer.close()
    await writer.wait_closed()


def _worker_error_response(request_id: str, message: str) -> ToolResponse:
    """Build one worker socket error response."""
    return ToolResponse(
        request_id=request_id,
        status="error",
        error_message=message,
    )


def _require_worker_connection(
    reader: asyncio.StreamReader | None,
    writer: asyncio.StreamWriter | None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Return the active worker socket streams or fail when disconnected."""
    if writer is None or reader is None:
        raise WorkerSocketError("Not connected")
    return reader, writer


def _payload_length(length_data: bytes) -> int:
    """Decode one big-endian payload length prefix."""
    (payload_len,) = struct.unpack(">I", length_data)
    return payload_len


def _checked_payload_length(payload_len: int) -> int:
    """Validate one payload length against the IPC size limit."""
    if payload_len > MAX_MESSAGE_SIZE:
        raise WorkerSocketError("Response exceeds size limit")
    return payload_len


def _signed_json_frame(message: ToolRequest | ToolResponse, secret: bytes) -> bytes:
    """Serialize and sign one IPC message model."""
    body = message.model_dump_json().encode()
    return sign_message(body, secret)


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
        handler: asyncio.coroutines,
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
        """Verify connecting peer via SO_PEERCRED.

        Args:
            writer: Stream writer for the connection.

        Returns:
            True if peer credentials match expectations.
        """
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
        """Handle an incoming worker connection.

        Args:
            reader: Stream reader for incoming data.
            writer: Stream writer for outgoing data.
        """
        if not self._verify_peer(writer):
            await _close_writer(writer)
            return

        try:
            while True:
                response = await self._read_and_process(reader)
                if response is None:
                    break
                await self._send_response(writer, response)
        except (
            ConnectionResetError,
            asyncio.IncompleteReadError,
        ):
            logger.debug("Worker disconnected")
        finally:
            await _close_writer(writer)

    async def _read_and_process(
        self, reader: asyncio.StreamReader
    ) -> ToolResponse | None:
        """Read one request, process it, return a response.

        Args:
            reader: Stream reader for incoming data.

        Returns:
            ToolResponse or None if connection closed.
        """
        length_data = await reader.readexactly(LENGTH_PREFIX_LEN)
        payload_len = _payload_length(length_data)

        if payload_len > MAX_MESSAGE_SIZE:
            logger.warning(
                "Oversized message rejected: %d bytes",
                payload_len,
            )
            return _worker_error_response("", "Message exceeds size limit")

        payload = await reader.readexactly(payload_len)
        message_bytes = verify_and_extract(payload, self.secret)
        request = ToolRequest.model_validate_json(message_bytes)

        if request.request_id not in self._pending_requests:
            logger.warning(
                "Unknown request_id: %s",
                request.request_id,
            )
            return _worker_error_response(
                request.request_id,
                "Unknown request_id",
            )

        self._pending_requests.discard(request.request_id)
        return await self.handler(request)

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        response: ToolResponse,
    ) -> None:
        """Send a signed response to the worker.

        Args:
            writer: Stream writer for outgoing data.
            response: Response to send.
        """
        frame = _signed_json_frame(response, self.secret)
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
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def send_request(self, request: ToolRequest) -> ToolResponse:
        """Send a tool request and wait for the response.

        Args:
            request: Tool execution request.

        Returns:
            Tool execution response.

        Raises:
            WorkerSocketError: If not connected or too large.
            AuthenticationError: If response HMAC is invalid.
            ValidationError: If response doesn't match schema.
        """
        reader, writer = _require_worker_connection(self._reader, self._writer)

        frame = _signed_json_frame(request, self.secret)
        writer.write(frame)
        await writer.drain()

        length_data = await reader.readexactly(LENGTH_PREFIX_LEN)
        payload_len = _checked_payload_length(_payload_length(length_data))

        payload = await reader.readexactly(payload_len)
        response_bytes = verify_and_extract(payload, self.secret)
        return ToolResponse.model_validate_json(response_bytes)
