"""Tests for WorkerSocket server/client communication.

Covers request/response over Unix domain sockets, HMAC
authentication, message size limits, peer verification,
and request_id binding.
"""

import asyncio
import os
import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from backend.ipc.protocol import (
    LENGTH_PREFIX_LEN,
    MAX_MESSAGE_SIZE,
    ToolRequest,
    ToolResponse,
    verify_and_extract,
)
from backend.ipc.socket import (
    WorkerSocketClient,
    WorkerSocketError,
    WorkerSocketServer,
)


@pytest.fixture
def socket_path(tmp_path):
    """Return a path for a test Unix socket."""
    return str(tmp_path / "test.sock")


@pytest.fixture
def secret():
    """Generate a test shared secret."""
    return os.urandom(32)


async def echo_handler(request: ToolRequest) -> ToolResponse:
    """Echo handler that returns request parameters as result."""
    return ToolResponse(
        request_id=request.request_id,
        status="success",
        result=request.parameters,
    )


class TestServerClientCommunication:
    """Tests for server-client request/response."""

    async def test_request_response_roundtrip(self, socket_path, secret):
        """Verify client sends request and receives response."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()

        request = ToolRequest(
            request_id="req-1",
            tool_name="test_tool",
            parameters={"key": "value"},
        )
        server.track_request("req-1")

        client = WorkerSocketClient(socket_path, secret)
        await client.connect()
        response = await client.send_request(request)
        await client.close()
        await server.stop()

        assert response.request_id == "req-1"
        assert response.status == "success"
        assert response.result == {"key": "value"}

    async def test_multiple_requests(self, socket_path, secret):
        """Verify multiple sequential requests work."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()

        client = WorkerSocketClient(socket_path, secret)
        await client.connect()

        for i in range(3):
            rid = f"req-{i}"
            server.track_request(rid)
            req = ToolRequest(
                request_id=rid,
                tool_name="test",
                parameters={"i": i},
            )
            resp = await client.send_request(req)
            assert resp.request_id == rid
            assert resp.status == "success"

        await client.close()
        await server.stop()

    async def test_socket_permissions(self, socket_path, secret):
        """Verify socket file has 0600 permissions."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()

        stat = os.stat(socket_path)
        assert oct(stat.st_mode & 0o777) == "0o600"

        await server.stop()


class TestClientErrors:
    """Tests for client error handling."""

    async def test_send_without_connect(self):
        """Verify sending without connection raises error."""
        client = WorkerSocketClient("/nonexistent.sock", b"secret")
        req = ToolRequest(
            request_id="req-1",
            tool_name="test",
            parameters={},
        )
        with pytest.raises(WorkerSocketError, match="Not connected"):
            await client.send_request(req)

    async def test_close_without_connect(self):
        """Verify close without connection is safe."""
        client = WorkerSocketClient("/nonexistent.sock", b"secret")
        await client.close()


class TestMessageSizeLimit:
    """Tests for bounded IPC frame size."""

    def test_ipc_message_size_limit_is_bounded(self):
        """Verify generic worker IPC frames are limited to 2 MB."""
        assert MAX_MESSAGE_SIZE == 2 * 1024 * 1024


class TestRequestIdBinding:
    """Tests for request_id verification."""

    async def test_unknown_request_id_rejected(self, socket_path, secret):
        """Verify untracked request_id returns error."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()

        client = WorkerSocketClient(socket_path, secret)
        await client.connect()

        req = ToolRequest(
            request_id="unknown-id",
            tool_name="test",
            parameters={},
        )
        resp = await client.send_request(req)
        assert resp.status == "error"
        assert "Unknown request_id" in (resp.error_message or "")

        await client.close()
        await server.stop()


class TestServerStop:
    """Tests for server cleanup."""

    async def test_stop_removes_socket(self, socket_path, secret):
        """Verify stop cleans up socket file."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()
        assert Path(socket_path).exists()
        await server.stop()
        assert not Path(socket_path).exists()

    async def test_start_removes_stale_socket(self, socket_path, secret):
        """Verify start removes existing socket file."""
        Path(socket_path).write_text("stale")
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()
        await server.stop()


class TestStopWithoutStart:
    """Tests for stop when server was never started."""

    async def test_stop_without_start(self, socket_path, secret):
        """Verify stop is safe when server was never started."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.stop()


class TestPeerVerification:
    """Tests for SO_PEERCRED peer verification."""

    async def test_wrong_peer_rejected(self, socket_path, secret):
        """Verify connection with wrong peer is rejected."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()
        server.set_expected_peer(pid=999999, uid=999999)

        reader, writer = await asyncio.open_unix_connection(socket_path)
        await asyncio.sleep(0.1)

        data = await reader.read(1)
        assert data == b""

        writer.close()
        await writer.wait_closed()
        await server.stop()

    async def test_correct_peer_accepted(self, socket_path, secret):
        """Verify connection with correct peer succeeds."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()
        server.set_expected_peer(pid=os.getpid(), uid=os.getuid())

        client = WorkerSocketClient(socket_path, secret)
        await client.connect()

        server.track_request("req-1")
        req = ToolRequest(
            request_id="req-1",
            tool_name="test",
            parameters={"ok": True},
        )
        resp = await client.send_request(req)
        assert resp.status == "success"

        await client.close()
        await server.stop()

    async def test_verify_peer_no_socket_info(self, socket_path, secret):
        """Verify peer check fails when socket info missing."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        server.set_expected_peer(pid=1, uid=1)

        mock_writer = MagicMock()
        mock_writer.get_extra_info.return_value = None
        assert not server._verify_peer(mock_writer)


class TestOversizedMessage:
    """Tests for message size limit enforcement."""

    async def test_server_rejects_oversized(self, socket_path, secret):
        """Verify server rejects messages exceeding size limit."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()

        reader, writer = await asyncio.open_unix_connection(socket_path)
        writer.write(struct.pack(">I", MAX_MESSAGE_SIZE + 1))
        await writer.drain()

        resp_len_data = await reader.readexactly(LENGTH_PREFIX_LEN)
        (resp_len,) = struct.unpack(">I", resp_len_data)
        resp_payload = await reader.readexactly(resp_len)
        resp_bytes = verify_and_extract(resp_payload, secret)
        resp = ToolResponse.model_validate_json(resp_bytes)

        assert resp.status == "error"
        assert "size limit" in (resp.error_message or "")

        writer.close()
        await writer.wait_closed()
        await server.stop()

    async def test_client_rejects_oversized(self, socket_path, secret):
        """Verify client raises on oversized response."""

        async def fake_handler(reader, writer):
            try:
                length_data = await reader.readexactly(LENGTH_PREFIX_LEN)
                (payload_len,) = struct.unpack(">I", length_data)
                await reader.readexactly(payload_len)
                writer.write(struct.pack(">I", MAX_MESSAGE_SIZE + 1))
                await writer.drain()
            except (
                ConnectionResetError,
                asyncio.IncompleteReadError,
            ):
                return
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(fake_handler, path=socket_path)

        client = WorkerSocketClient(socket_path, secret)
        await client.connect()

        req = ToolRequest(
            request_id="req-1",
            tool_name="test",
            parameters={},
        )
        with pytest.raises(WorkerSocketError, match="size limit"):
            await client.send_request(req)

        await client.close()
        server.close()
        await server.wait_closed()


class TestNoneResponse:
    """Tests for connection handling when read returns None."""

    async def test_connection_closes_on_none(self, socket_path, secret):
        """Verify connection closes when read returns None."""
        server = WorkerSocketServer(socket_path, secret, echo_handler)
        await server.start()

        async def mock_read(_reader):
            return None

        server._read_and_process = mock_read

        reader, writer = await asyncio.open_unix_connection(socket_path)
        await asyncio.sleep(0.1)

        data = await reader.read(1)
        assert data == b""

        writer.close()
        await writer.wait_closed()
        await server.stop()
