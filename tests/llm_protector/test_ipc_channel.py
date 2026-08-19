"""Tests for generic full-duplex IPC channel.

Covers basic send/receive, full-duplex server push, type-discriminated
message routing, and error handling for unknown message types.
"""

import asyncio
import secrets

import pytest
from pydantic import BaseModel, ConfigDict

from backend.ipc.ipc_channel import (
    IPCChannelError,
    IPCSocketClient,
    IPCSocketServer,
)


class PingRequest(BaseModel):
    """Test request model."""

    model_config = ConfigDict(extra="forbid")
    request_id: str
    message: str


class PongResponse(BaseModel):
    """Test response model."""

    model_config = ConfigDict(extra="forbid")
    request_id: str
    reply: str


class PushEvent(BaseModel):
    """Test server-push model."""

    model_config = ConfigDict(extra="forbid")
    request_id: str
    data: str


SERVER_TYPE_MAP = {
    "PingRequest": PingRequest,
}

CLIENT_TYPE_MAP = {
    "PongResponse": PongResponse,
    "PushEvent": PushEvent,
}

SHARED_SECRET = secrets.token_bytes(32)


@pytest.fixture
def socket_path(tmp_path):
    """Create a temporary socket path."""
    return str(tmp_path / "test.sock")


class TestIPCChannel:
    """Tests for IPCSocketServer and IPCSocketClient."""

    @pytest.mark.asyncio
    async def test_send_and_receive(self, socket_path):
        """Send a request and receive a response."""

        async def handler(type_name, message):
            """Echo handler that returns PongResponse."""
            return PongResponse(
                request_id=message.request_id,
                reply="pong: " + message.message,
            )

        server = IPCSocketServer(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            handler=handler,
            type_map=SERVER_TYPE_MAP,
        )
        await server.start()

        client = IPCSocketClient(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            type_map=CLIENT_TYPE_MAP,
        )
        await client.connect()

        try:
            response = await client.send_and_receive(
                PingRequest(request_id="r1", message="hello")
            )
            assert isinstance(response, PongResponse)
            assert response.request_id == "r1"
            assert response.reply == "pong: hello"
        finally:
            await client.close()
            await server.stop()

    @pytest.mark.asyncio
    async def test_send_and_receive_matching_discards_stale_push(self, socket_path):
        """Ignore a stale pushed message while waiting for the matching response."""
        server: IPCSocketServer

        async def handler(type_name, message):
            """Push a stale event before returning the matching response."""
            await server.push(PushEvent(request_id="stale", data="old"))
            return PongResponse(
                request_id=message.request_id,
                reply="pong: " + message.message,
            )

        server = IPCSocketServer(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            handler=handler,
            type_map=SERVER_TYPE_MAP,
        )
        await server.start()

        client = IPCSocketClient(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            type_map=CLIENT_TYPE_MAP,
        )
        await client.connect()

        try:
            response = await client.send_and_receive_matching(
                PingRequest(request_id="r1", message="hello")
            )
            assert isinstance(response, PongResponse)
            assert response.request_id == "r1"
            assert response.reply == "pong: hello"
        finally:
            await client.close()
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_push(self, socket_path):
        """Server pushes a message without prior request."""

        async def handler(type_name, message):
            """Handler that does not reply."""
            return

        server = IPCSocketServer(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            handler=handler,
            type_map=SERVER_TYPE_MAP,
        )
        await server.start()

        client = IPCSocketClient(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            type_map=CLIENT_TYPE_MAP,
        )
        await client.connect()

        try:
            # Send a request so the server knows about the client
            await client.send(PingRequest(request_id="r1", message="hi"))
            # Give server time to process
            await asyncio.sleep(0.1)

            # Server pushes an event
            await server.push(PushEvent(request_id="r1", data="token-1"))

            # Client receives the pushed event
            event = await client.receive()
            assert isinstance(event, PushEvent)
            assert event.data == "token-1"
        finally:
            await client.close()
            await server.stop()

    @pytest.mark.asyncio
    async def test_multiple_request_response(self, socket_path):
        """Send multiple requests and receive corresponding responses."""

        async def handler(type_name, message):
            """Echo handler."""
            return PongResponse(
                request_id=message.request_id,
                reply=message.message.upper(),
            )

        server = IPCSocketServer(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            handler=handler,
            type_map=SERVER_TYPE_MAP,
        )
        await server.start()

        client = IPCSocketClient(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            type_map=CLIENT_TYPE_MAP,
        )
        await client.connect()

        try:
            r1 = await client.send_and_receive(
                PingRequest(request_id="1", message="foo")
            )
            r2 = await client.send_and_receive(
                PingRequest(request_id="2", message="bar")
            )
            assert r1.reply == "FOO"
            assert r2.reply == "BAR"
        finally:
            await client.close()
            await server.stop()

    @pytest.mark.asyncio
    async def test_client_not_connected(self):
        """Raise error when sending on unconnected client."""
        client = IPCSocketClient(
            socket_path="/nonexistent.sock",
            secret=SHARED_SECRET,
            type_map=CLIENT_TYPE_MAP,
        )
        with pytest.raises(IPCChannelError, match="Not connected"):
            await client.send(PingRequest(request_id="1", message="hi"))

    @pytest.mark.asyncio
    async def test_server_push_no_client(self, socket_path):
        """Raise error when pushing without a connected client."""

        async def handler(type_name, message):
            """Stub handler."""
            return

        server = IPCSocketServer(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            handler=handler,
            type_map=SERVER_TYPE_MAP,
        )
        await server.start()

        try:
            with pytest.raises(IPCChannelError, match="No client connected"):
                await server.push(PushEvent(request_id="1", data="test"))
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_has_client_property(self, socket_path):
        """Track client connection status."""

        async def handler(type_name, message):
            """Stub handler."""
            return

        server = IPCSocketServer(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            handler=handler,
            type_map=SERVER_TYPE_MAP,
        )
        await server.start()
        assert server.has_client is False

        client = IPCSocketClient(
            socket_path=socket_path,
            secret=SHARED_SECRET,
            type_map=CLIENT_TYPE_MAP,
        )
        await client.connect()
        # Send something to trigger the server's connection handler
        await client.send(PingRequest(request_id="1", message="hi"))
        await asyncio.sleep(0.1)
        assert server.has_client is True

        await client.close()
        await asyncio.sleep(0.1)
        await server.stop()
