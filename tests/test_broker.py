"""Tests for tool broker.

Covers tool execution, validation, rate limiting,
unknown tools, handler routing, and audit logging.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.tools.broker import (
    ToolBroker,
    ToolBrokerError,
)
from backend.tools.rate_limits import (
    ToolRateLimiter,
)
from backend.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    WorkerTarget,
)
from backend.trust.zones import TrustZone
from pydantic import BaseModel


class _EchoInput(BaseModel, extra="forbid"):
    """Simple input model for testing."""

    message: str


def _setup_broker(audit_fn=None):
    """Create a broker with a registered echo tool."""
    registry = ToolRegistry()
    tool = ToolDefinition(
        name="echo",
        description="Echoes input",
        input_model=_EchoInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    )
    registry.register(tool)
    broker = ToolBroker(registry=registry, audit_fn=audit_fn)

    async def echo_handler(validated):
        return {"echo": validated.message}

    broker.set_handler("echo", echo_handler)
    return broker


class TestExecute:
    """Tests for tool execution."""

    async def test_successful_execution(self):
        """Verify tool executes and returns result."""
        broker = _setup_broker()
        result = await broker.execute("echo", {"message": "hello"})
        assert result.success
        assert result.result == {"echo": "hello"}
        assert result.zone == TrustZone.GREEN
        assert result.duration_ms >= 0

    async def test_unknown_tool(self):
        """Verify unknown tool raises error."""
        broker = _setup_broker()
        with pytest.raises(ToolBrokerError, match="Unknown"):
            await broker.execute("nonexistent", {})

    async def test_validation_failure(self):
        """Verify invalid params raise error."""
        broker = _setup_broker()
        with pytest.raises(ToolBrokerError, match="Validation"):
            await broker.execute("echo", {"bad": "param"})

    async def test_no_handler(self):
        """Verify missing handler raises error."""
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="no_handler",
            description="No handler",
            input_model=_EchoInput,
            zone=TrustZone.GREEN,
            worker=WorkerTarget.CORE,
        )
        registry.register(tool)
        broker = ToolBroker(registry=registry)

        with pytest.raises(ToolBrokerError, match="No handler"):
            await broker.execute("no_handler", {"message": "test"})


class TestRateLimiting:
    """Tests for rate limit enforcement in broker."""

    async def test_rate_limit_blocks(self):
        """Verify rate-limited calls are blocked."""
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="limited",
            description="Limited tool",
            input_model=_EchoInput,
            zone=TrustZone.GREEN,
            worker=WorkerTarget.CORE,
        )
        registry.register(tool)

        limiter = ToolRateLimiter()
        # Exhaust the unknown tool limit (1)
        limiter.record("limited")
        broker = ToolBroker(registry=registry, rate_limiter=limiter)

        with pytest.raises(ToolBrokerError, match="Rate limit"):
            await broker.execute("limited", {"message": "test"})


_FILTER_MOD = "backend.security.content_filter"


class TestAuditLogging:
    """Tests for audit logging in broker."""

    @patch(
        f"{_FILTER_MOD}.filter_output",
    )
    async def test_success_logs_audit(
        self,
        mock_filter,
    ):
        """Verify successful execute logs tool_success."""
        mock_filter.return_value = MagicMock(text='{"message": "hello"}')
        audit_fn = AsyncMock()
        broker = _setup_broker(audit_fn=audit_fn)

        await broker.execute("echo", {"message": "hello"})
        await asyncio.sleep(0)

        audit_fn.assert_awaited_once()
        call_args = audit_fn.await_args
        assert call_args.args == ("tool_success", "echo")
        assert call_args.kwargs["zone"] == "green"

    @patch(
        f"{_FILTER_MOD}.filter_output",
    )
    async def test_unknown_tool_logs_rejected(
        self,
        mock_filter,
    ):
        """Verify unknown tool logs tool_rejected."""
        mock_filter.return_value = MagicMock(text="{}")
        audit_fn = AsyncMock()
        broker = _setup_broker(audit_fn=audit_fn)

        with pytest.raises(ToolBrokerError):
            await broker.execute("nonexistent", {})
        await asyncio.sleep(0)

        audit_fn.assert_awaited_once()
        call_args = audit_fn.await_args
        assert call_args.args == ("tool_rejected", "nonexistent")

    async def test_missing_audit_fn_skips_audit(self):
        """Verify missing audit callback skips audit without error."""
        broker = _setup_broker(audit_fn=None)

        result = await broker.execute("echo", {"message": "hello"})
        assert result.success

    async def test_credential_in_result_is_redacted(self):
        """Verify credential-like strings in tool results are redacted in audit."""
        # API key pattern: sk- followed by 20+ alphanumeric chars
        fake_key = "sk-" + "a" * 25
        audit_fn = AsyncMock()
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="leaky",
            description="Returns a credential",
            input_model=_EchoInput,
            zone=TrustZone.GREEN,
            worker=WorkerTarget.CORE,
        )
        registry.register(tool)
        broker = ToolBroker(registry=registry, audit_fn=audit_fn)

        async def leaky_handler(validated):
            return {"secret": fake_key}

        broker.set_handler("leaky", leaky_handler)
        await broker.execute("leaky", {"message": "go"})
        await asyncio.sleep(0)

        audit_fn.assert_awaited_once()
        result_summary = audit_fn.await_args.kwargs.get("result", "")
        assert fake_key not in (result_summary or ""), (
            f"Raw credential leaked into audit: {result_summary}"
        )


class TestRateLimitReset:
    """Tests for rate limit reset on broker."""

    async def test_reset_allows_continued_execution(self):
        """Verify reset_rate_limit unblocks a tool."""
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="limited",
            description="Limited tool",
            input_model=_EchoInput,
            zone=TrustZone.GREEN,
            worker=WorkerTarget.CORE,
        )
        registry.register(tool)

        limiter = ToolRateLimiter()
        limiter.record("limited")
        broker = ToolBroker(
            registry=registry,
            rate_limiter=limiter,
        )

        async def handler(v):
            return {"echo": v.message}

        broker.set_handler("limited", handler)

        with pytest.raises(ToolBrokerError, match="Rate limit"):
            await broker.execute("limited", {"message": "blocked"})

        broker.reset_rate_limit("limited")

        result = await broker.execute("limited", {"message": "unblocked"})
        assert result.success
        assert result.result == {
            "echo": "unblocked",
        }
