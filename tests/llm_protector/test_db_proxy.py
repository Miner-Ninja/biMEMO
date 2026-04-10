"""Tests for OperationRegistry, ChannelAllowlist, and health check.

Covers operation dispatch, unknown operation rejection, allowlist
enforcement per channel, and health check handler.
"""

import pytest
from backend.ipc.db_proxy import (
    ChannelAllowlist,
    OperationNotAllowedError,
    OperationRegistry,
    create_health_handler,
)
from backend.ipc.models import DBOperation


class TestOperationRegistry:
    """Tests for the OperationRegistry."""

    @pytest.mark.asyncio
    async def test_dispatch_registered_operation(self):
        """Dispatch a registered operation successfully."""
        registry = OperationRegistry()

        async def handle_save(params):
            """Save handler stub."""
            return {"message_id": "m1"}

        registry.register("save_message", handle_save)
        result = await registry.dispatch(
            DBOperation(
                request_id="req-1",
                operation="save_message",
                parameters={"content": "hello"},
            )
        )
        assert result.status == "success"
        assert result.data == {"message_id": "m1"}
        assert result.request_id == "req-1"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_operation(self):
        """Reject an unknown operation with error."""
        registry = OperationRegistry()
        result = await registry.dispatch(
            DBOperation(
                request_id="req-1",
                operation="drop_table",
            )
        )
        assert result.status == "error"
        assert "Unknown operation" in result.error_message
        assert registry.stats.rejected_operations == 1

    @pytest.mark.asyncio
    async def test_dispatch_handler_exception(self):
        """Return error when handler raises an exception."""
        registry = OperationRegistry()

        async def handle_fail(params):
            """Failing handler stub."""
            raise ValueError("DB connection lost")

        registry.register("failing_op", handle_fail)
        result = await registry.dispatch(
            DBOperation(request_id="req-1", operation="failing_op")
        )
        assert result.status == "error"
        assert "DB connection lost" in result.error_message
        assert registry.stats.failed_operations == 1

    def test_has_operation(self):
        """Check existence of registered operations."""
        registry = OperationRegistry()

        async def handler(params):
            """Stub handler."""
            return {}

        registry.register("save_message", handler)
        assert registry.has_operation("save_message") is True
        assert registry.has_operation("drop_table") is False

    def test_list_operations(self):
        """List all registered operation names."""
        registry = OperationRegistry()

        async def handler(params):
            """Stub handler."""
            return {}

        registry.register("op_a", handler)
        registry.register("op_b", handler)
        ops = registry.list_operations()
        assert "op_a" in ops
        assert "op_b" in ops
        assert len(ops) == 2

    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        """Track execution statistics across dispatches."""
        registry = OperationRegistry()

        async def handler(params):
            """Stub handler."""
            return {}

        registry.register("op", handler)
        await registry.dispatch(DBOperation(request_id="1", operation="op"))
        await registry.dispatch(DBOperation(request_id="2", operation="op"))
        await registry.dispatch(DBOperation(request_id="3", operation="unknown"))
        assert registry.stats.total_operations == 2
        assert registry.stats.rejected_operations == 1


class TestChannelAllowlist:
    """Tests for the ChannelAllowlist."""

    def test_allowed_operation(self):
        """Allow an operation that is in the channel allowlist."""
        allowlist = ChannelAllowlist()
        allowlist.set_allowed("socket_a", {"save_message", "load_context"})
        assert allowlist.is_allowed("socket_a", "save_message") is True

    def test_disallowed_operation(self):
        """Deny an operation that is not in the channel allowlist."""
        allowlist = ChannelAllowlist()
        allowlist.set_allowed("socket_a", {"save_message"})
        assert allowlist.is_allowed("socket_a", "get_credential") is False

    def test_check_raises_on_denial(self):
        """Raise OperationNotAllowedError when operation is denied."""
        allowlist = ChannelAllowlist()
        allowlist.set_allowed("socket_a", {"save_message"})
        with pytest.raises(OperationNotAllowedError, match="not allowed"):
            allowlist.check("socket_a", "get_credential")

    def test_check_passes_on_allowed(self):
        """Pass without error when operation is allowed."""
        allowlist = ChannelAllowlist()
        allowlist.set_allowed("socket_a", {"save_message"})
        allowlist.check("socket_a", "save_message")

    def test_unknown_channel(self):
        """Deny operations on unconfigured channels."""
        allowlist = ChannelAllowlist()
        assert allowlist.is_allowed("unknown", "save_message") is False

    def test_separate_channel_allowlists(self):
        """Each channel has independent allowlists."""
        allowlist = ChannelAllowlist()
        allowlist.set_allowed("socket_a", {"save_message", "load_context"})
        allowlist.set_allowed("socket_b", {"get_credential", "store_credential"})

        assert allowlist.is_allowed("socket_a", "save_message") is True
        assert allowlist.is_allowed("socket_a", "get_credential") is False
        assert allowlist.is_allowed("socket_b", "get_credential") is True
        assert allowlist.is_allowed("socket_b", "save_message") is False

    def test_get_allowed(self):
        """Return the set of allowed operations for a channel."""
        allowlist = ChannelAllowlist()
        allowlist.set_allowed("socket_a", {"op1", "op2"})
        assert allowlist.get_allowed("socket_a") == {"op1", "op2"}
        assert allowlist.get_allowed("unknown") == set()


class TestHealthHandler:
    """Tests for the health check handler factory."""

    @pytest.mark.asyncio
    async def test_healthy(self):
        """Return healthy status when DB is connected."""
        registry = OperationRegistry()

        async def handler(params):
            """Stub handler."""
            return {}

        registry.register("op_a", handler)

        async def db_ok():
            """Stub DB check."""
            return True

        health = create_health_handler(registry, db_ok)
        result = await health(None)
        assert result["status"] == "healthy"
        assert result["db_connected"] is True
        assert result["operations_registered"] == 1

    @pytest.mark.asyncio
    async def test_unhealthy(self):
        """Return unhealthy status when DB is disconnected."""
        registry = OperationRegistry()

        async def db_fail():
            """Stub failing DB check."""
            return False

        health = create_health_handler(registry, db_fail)
        result = await health(None)
        assert result["status"] == "unhealthy"
        assert result["db_connected"] is False
