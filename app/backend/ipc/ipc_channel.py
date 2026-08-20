"""Generic full-duplex IPC channel over Unix domain sockets.

Provides IPCSocketServer and IPCSocketClient for sending and receiving
arbitrary Pydantic models over HMAC-authenticated Unix sockets. Supports
full-duplex communication where both sides can send messages independently,
correlated by request_id. Unlike the worker-specific WorkerSocketServer,
this channel supports multiple message types via a JSON envelope with
a _type discriminator field.
"""

import asyncio
import json
import logging
import os
import struct
from collections.abc import Callable, Coroutine
from typing import Any

from pydantic import BaseModel

from backend.ipc.protocol import (
    LENGTH_PREFIX_LEN,
    MAX_MESSAGE_SIZE,
    AuthenticationError,
    sign_message,
    verify_and_extract,
)


logger = logging.getLogger(__name__)

# --- Constants ---
TYPE_FIELD = "_type"
PAYLOAD_FIELD = "_payload"


class IPCChannelError(Exception):
    """Raised when IPC channel communication fails."""


MessageHandler = Callable[
    [str, BaseModel],
    Coroutine[Any, Any, BaseModel | None],
]


def _encode_envelope(message: BaseModel) -> bytes:
    """Wrap a Pydantic model in a typed JSON envelope.

    Args:
        message: Pydantic model to encode.

    Returns:
        JSON bytes with _type and _payload fields.
    """
    envelope = {
        TYPE_FIELD: type(message).__name__,
        PAYLOAD_FIELD: json.loads(message.model_dump_json()),
    }
    return json.dumps(envelope, separators=(",", ":")).encode()


def _decode_envelope(
    data: bytes,
    type_map: dict[str, type[BaseModel]],
) -> BaseModel:
    """Decode a typed JSON envelope back to a Pydantic model.

    Args:
        data: JSON bytes with _type and _payload fields.
        type_map: Mapping from type name to Pydantic model class.

    Returns:
        Deserialized Pydantic model instance.

    Raises:
        IPCChannelError: If type is unknown or validation fails.
    """
    envelope = json.loads(data)
    type_name = envelope.get(TYPE_FIELD)
    if type_name is None:
        raise IPCChannelError("Message missing _type field")

    model_class = type_map.get(type_name)
    if model_class is None:
        raise IPCChannelError(f"Unknown message type: '{type_name}'")

    return model_class.model_validate(envelope[PAYLOAD_FIELD])


def _message_type_name(message: BaseModel) -> str:
    """Return the discriminator type name for one IPC message."""
    return type(message).__name__


async def _read_message(
    reader: asyncio.StreamReader,
    secret: bytes,
    type_map: dict[str, type[BaseModel]],
) -> BaseModel:
    """Read and decode one HMAC-authenticated message from the stream.

    Args:
        reader: Async stream reader.
        secret: HMAC shared secret.
        type_map: Type discriminator map.

    Returns:
        Decoded Pydantic model.

    Raises:
        IPCChannelError: On protocol errors.
        AuthenticationError: On HMAC failure.
    """
    length_data = await reader.readexactly(LENGTH_PREFIX_LEN)
    (payload_len,) = struct.unpack(">I", length_data)

    if payload_len > MAX_MESSAGE_SIZE:
        raise IPCChannelError(
            f"Message exceeds size limit: {payload_len} bytes",
        )

    payload = await reader.readexactly(payload_len)
    message_bytes = verify_and_extract(payload, secret)
    return _decode_envelope(message_bytes, type_map)


async def _write_message(
    writer: asyncio.StreamWriter,
    message: BaseModel,
    secret: bytes,
) -> None:
    """Encode and write one HMAC-authenticated message to the stream.

    Args:
        writer: Async stream writer.
        message: Pydantic model to send.
        secret: HMAC shared secret.
    """
    body = _encode_envelope(message)
    frame = sign_message(body, secret)
    writer.write(frame)
    await writer.drain()


def _require_reader(
    reader: asyncio.StreamReader | None,
    *,
    error_message: str = "Not connected",
) -> asyncio.StreamReader:
    """Return the connected reader or fail when no connection exists."""
    if reader is None:
        raise IPCChannelError(error_message)
    return reader


def _require_writer(
    writer: asyncio.StreamWriter | None,
    *,
    error_message: str = "Not connected",
) -> asyncio.StreamWriter:
    """Return the connected writer or fail when no connection exists."""
    if writer is None:
        raise IPCChannelError(error_message)
    return writer


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Close one stream writer and wait until the close finishes."""
    writer.close()
    await writer.wait_closed()


def _clear_stream_pair(
    owner: object,
    *,
    reader_attr: str = "_reader",
    writer_attr: str = "_writer",
) -> None:
    """Clear stored reader-writer connection state on one IPC peer."""
    if hasattr(owner, reader_attr):
        setattr(owner, reader_attr, None)
    if hasattr(owner, writer_attr):
        setattr(owner, writer_attr, None)


class IPCSocketServer:
    """Generic full-duplex IPC server over Unix domain socket.

    Accepts a single client connection and dispatches incoming messages
    to a handler based on their type. Can also push messages to the
    connected client without a prior request.

    Attributes:
        socket_path: Filesystem path for the Unix socket.
        secret: HMAC shared secret.
        handler: Async callable(type_name, model) -> optional response model.
        type_map: Mapping from type name string to Pydantic model class.
    """

    def __init__(
        self,
        socket_path: str,
        secret: bytes,
        handler: MessageHandler,
        type_map: dict[str, type[BaseModel]],
    ) -> None:
        """Initialize the IPC server.

        Args:
            socket_path: Path for the Unix domain socket file.
            secret: 32-byte HMAC shared secret.
            handler: Async function(type_name, model) -> optional response.
            type_map: Type discriminator mapping for incoming messages.
        """
        self.socket_path = socket_path
        self.secret = secret
        self.handler = handler
        self.type_map = type_map
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._writer_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start listening on the Unix domain socket."""
        if os.path.exists(self.socket_path):  # noqa: ASYNC240 — single stat of the socket file at start/stop
            os.unlink(self.socket_path)

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self.socket_path,
        )
        os.chmod(self.socket_path, 0o600)
        logger.info("IPC server started at %s", self.socket_path)

    async def stop(self) -> None:
        """Stop the server and clean up."""
        if self._writer is not None:
            await _close_writer(self._writer)
            self._writer = None

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if os.path.exists(self.socket_path):  # noqa: ASYNC240 — single stat of the socket file at start/stop
            os.unlink(self.socket_path)

    async def push(self, message: BaseModel) -> None:
        """Push a message to the connected client.

        Args:
            message: Pydantic model to send.

        Raises:
            IPCChannelError: If no client is connected.
        """
        writer = _require_writer(self._writer, error_message="No client connected")
        async with self._writer_lock:
            await _write_message(writer, message, self.secret)

    @property
    def has_client(self) -> bool:
        """Check if a client is currently connected."""
        return self._writer is not None

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming client connection."""
        self._writer = writer
        try:
            while True:
                message = await _read_message(reader, self.secret, self.type_map)
                type_name = _message_type_name(message)
                response = await self.handler(type_name, message)
                if response is not None:
                    async with self._writer_lock:
                        await _write_message(writer, response, self.secret)
        except (
            ConnectionResetError,
            asyncio.IncompleteReadError,
            AuthenticationError,
        ) as e:
            logger.debug("IPC client disconnected: %s", e)
        finally:
            _clear_stream_pair(self, reader_attr="_reader", writer_attr="_writer")
            await _close_writer(writer)


class IPCSocketClient:
    """Generic full-duplex IPC client over Unix domain socket.

    Connects to an IPCSocketServer and supports sending messages
    and receiving responses or server-pushed events.

    Attributes:
        socket_path: Path to the server Unix domain socket.
        secret: HMAC shared secret.
        type_map: Mapping from type name string to Pydantic model class.
    """

    def __init__(
        self,
        socket_path: str,
        secret: bytes,
        type_map: dict[str, type[BaseModel]],
    ) -> None:
        """Initialize the IPC client.

        Args:
            socket_path: Path to the Unix domain socket.
            secret: 32-byte HMAC shared secret.
            type_map: Type discriminator mapping for incoming messages.
        """
        self.socket_path = socket_path
        self.secret = secret
        self.type_map = type_map
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._rpc_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect to the IPC server."""
        self._reader, self._writer = await asyncio.open_unix_connection(
            self.socket_path
        )

    async def close(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            await _close_writer(self._writer)
            _clear_stream_pair(self)

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server."""
        return self._writer is not None

    async def send(self, message: BaseModel) -> None:
        """Send a message to the server.

        Args:
            message: Pydantic model to send.

        Raises:
            IPCChannelError: If not connected.
        """
        writer = _require_writer(self._writer)
        async with self._write_lock:
            await _write_message(writer, message, self.secret)

    async def receive(self) -> BaseModel:
        """Wait for and receive one message from the server.

        Returns:
            Decoded Pydantic model.

        Raises:
            IPCChannelError: If not connected or protocol error.
        """
        reader = _require_reader(self._reader)
        return await _read_message(reader, self.secret, self.type_map)

    async def send_and_receive(self, message: BaseModel) -> BaseModel:
        """Send a message and wait for one response.

        Serializes concurrent RPC calls so that each send-receive
        pair is atomic, preventing response mix-ups.

        Args:
            message: Pydantic model to send.

        Returns:
            Response Pydantic model.
        """
        async with self._rpc_lock:
            await self.send(message)
            return await self.receive()

    async def send_and_receive_matching(self, message: BaseModel) -> BaseModel:
        """Send a request and wait for the response with the same request_id.

        A cancelled caller can leave an already-sent response unread in the
        stream. Plain send-and-receive would hand that stale response to the
        next RPC. This method drains mismatched responses until the current
        request's response arrives.
        """
        expected_request_id = str(getattr(message, "request_id", ""))
        async with self._rpc_lock:
            await self.send(message)
            while True:
                response = await self.receive()
                response_request_id = str(getattr(response, "request_id", ""))
                if (
                    not expected_request_id
                    or response_request_id == expected_request_id
                ):
                    return response
                logger.warning(
                    "Discarding stale IPC response: expected request_id=%s got=%s",
                    expected_request_id,
                    response_request_id,
                )
