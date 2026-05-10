"""Tests for worker process lifecycle management.

Covers worker spawning, health checking, restart behavior,
secret generation, and default handler.
"""

import asyncio
import multiprocessing
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from backend.bus.worker_manager import WorkerManager
from backend.ipc.protocol import ToolRequest, ToolResponse


if TYPE_CHECKING:
    import multiprocessing.connection


def dummy_worker(socket_path: str, conn: multiprocessing.connection.Connection) -> None:
    """Minimal worker that reads secret and exits."""
    conn.recv_bytes()
    conn.close()
    time.sleep(0.1)


def long_running_worker(
    socket_path: str, conn: multiprocessing.connection.Connection
) -> None:
    """Worker that runs until terminated."""
    conn.recv_bytes()
    conn.close()
    time.sleep(60)


class TestWorkerSpawning:
    """Tests for worker process creation."""

    async def test_start_creates_process(self, tmp_path):
        """Verify start spawns a subprocess."""
        mgr = WorkerManager(
            "test-worker",
            long_running_worker,
            socket_dir=str(tmp_path),
        )
        await mgr.start()
        assert mgr.is_alive
        await mgr.stop()

    async def test_stop_terminates_process(self, tmp_path):
        """Verify stop terminates the worker."""
        mgr = WorkerManager(
            "test-worker",
            long_running_worker,
            socket_dir=str(tmp_path),
        )
        await mgr.start()
        await mgr.stop()
        assert not mgr.is_alive

    async def test_socket_path(self, tmp_path):
        """Verify socket path is constructed correctly."""
        mgr = WorkerManager(
            "my-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        expected = str(tmp_path / "my-worker.sock")
        assert mgr.socket_path == expected


class TestHealthCheck:
    """Tests for worker health monitoring."""

    async def test_detects_dead_worker(self, tmp_path):
        """Verify is_alive returns False after worker exits."""
        mgr = WorkerManager(
            "short-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        await mgr.start()
        # Wait for dummy worker to finish (generous for CI/container)
        await asyncio.sleep(2.0)
        assert not mgr.is_alive
        await mgr.stop()

    async def test_is_alive_false_when_not_started(self, tmp_path):
        """Verify is_alive is False before start."""
        mgr = WorkerManager(
            "not-started",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        assert not mgr.is_alive


class TestDefaultHandler:
    """Tests for the default request handler."""

    async def test_default_handler_returns_error(self, tmp_path):
        """Verify default handler returns error response."""
        mgr = WorkerManager(
            "test-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        request = ToolRequest(
            request_id="req-1",
            tool_name="test",
            parameters={},
        )
        response = await mgr._default_handler(request)
        assert response.status == "error"
        assert "No handler" in (response.error_message or "")

    async def test_send_request_without_start(self, tmp_path):
        """Verify send_request before start returns error."""
        mgr = WorkerManager(
            "not-started",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        request = ToolRequest(
            request_id="req-1",
            tool_name="test",
            parameters={},
        )
        response = await mgr.send_request(request)
        assert response.status == "error"
        assert "not started" in (response.error_message or "")


class TestCustomHandler:
    """Tests for custom request handler."""

    async def test_set_handler(self, tmp_path):
        """Verify custom handler is called."""
        mgr = WorkerManager(
            "custom-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )

        async def custom(req: ToolRequest) -> ToolResponse:
            return ToolResponse(
                request_id=req.request_id,
                status="success",
                result={"custom": True},
            )

        mgr.set_handler(custom)
        await mgr.start()

        request = ToolRequest(
            request_id="req-1",
            tool_name="test",
            parameters={},
        )
        response = await mgr.send_request(request)
        assert response.status == "success"
        assert response.result == {"custom": True}
        await mgr.stop()


class TestStopWithoutStart:
    """Tests for stop when never started."""

    async def test_stop_without_start(self, tmp_path):
        """Verify stop is safe when never started."""
        mgr = WorkerManager(
            "test-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        await mgr.stop()
        assert not mgr.is_alive


class TestTerminateAndKill:
    """Tests for process termination edge cases."""

    async def test_kill_after_terminate_timeout(self, tmp_path):
        """Verify kill is called when terminate fails."""
        mgr = WorkerManager(
            "test-worker",
            long_running_worker,
            socket_dir=str(tmp_path),
        )
        await mgr.start()

        real_process = mgr._process
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        mgr._process = mock_process

        await mgr.stop()

        mock_process.terminate.assert_called_once()
        mock_process.join.assert_called_once_with(timeout=5)
        mock_process.kill.assert_called_once()

        real_process.terminate()
        real_process.join(timeout=5)


class TestHealthRestart:
    """Tests for automatic worker restart."""

    async def test_restarts_dead_worker(self, tmp_path):
        """Verify health loop detects and restarts crashed worker."""
        mgr = WorkerManager(
            "test-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        with patch(
            "backend.bus.worker_manager.HEALTH_CHECK_INTERVAL",
            0.3,
        ):
            await mgr.start()
            await asyncio.sleep(1.5)
        await mgr.stop()
        assert mgr._restart_count >= 1

    async def test_max_restart_exceeded(self, tmp_path):
        """Verify worker stops after max restart attempts."""
        mgr = WorkerManager(
            "test-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        with (
            patch(
                "backend.bus.worker_manager.HEALTH_CHECK_INTERVAL",
                0.2,
            ),
            patch(
                "backend.bus.worker_manager.MAX_RESTART_ATTEMPTS",
                2,
            ),
        ):
            await mgr.start()
            await asyncio.sleep(5.0)
            assert not mgr._running
        await mgr.stop()


class TestHealthLoopEdgeCases:
    """Tests for health loop edge cases."""

    async def test_exits_when_not_running(self, tmp_path):
        """Verify health loop exits when not running."""
        mgr = WorkerManager(
            "test-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        mgr._running = False
        await mgr._health_loop()

    async def test_stops_mid_sleep(self, tmp_path):
        """Verify health loop exits when stopped during sleep."""
        mgr = WorkerManager(
            "test-worker",
            long_running_worker,
            socket_dir=str(tmp_path),
        )
        mgr._running = True
        mgr._process = MagicMock()
        mgr._process.is_alive.return_value = True

        async def stop_during_sleep(_delay):
            mgr._running = False

        with patch(
            "backend.bus.worker_manager.asyncio.sleep",
            stop_during_sleep,
        ):
            await mgr._health_loop()


class TestSpawnEdgeCases:
    """Tests for spawn edge cases."""

    async def test_spawn_without_socket_server(self, tmp_path):
        """Verify spawn works when socket_server is None."""
        mgr = WorkerManager(
            "test-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        mgr._secret = os.urandom(32)
        mgr._spawn_worker()
        assert mgr._process is not None
        mgr._process.terminate()
        mgr._process.join(timeout=5)

    async def test_restart_without_socket_server(self, tmp_path):
        """Verify restart works when socket_server is None."""
        mgr = WorkerManager(
            "test-worker",
            dummy_worker,
            socket_dir=str(tmp_path),
        )
        mgr._running = True
        mgr._secret = os.urandom(32)
        await mgr._restart()
        assert mgr._restart_count == 1
        if mgr._process and mgr._process.is_alive():
            mgr._process.terminate()
            mgr._process.join(timeout=5)
