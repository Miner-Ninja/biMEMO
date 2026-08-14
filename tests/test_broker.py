"""Tests for tool broker.

Covers tool execution, validation, rate limiting,
unknown tools, handler routing, and audit logging.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from backend.tools.broker import (
    AuthorizationContext,
    ToolBroker,
    ToolBrokerError,
)
from backend.tools.rate_limits import (
    ToolRateLimiter,
)
from backend.tools.registry import (
    PermissionMode,
    PermissionPolicy,
    ToolDefinition,
    ToolRegistry,
)


class _EchoInput(BaseModel, extra="forbid", frozen=True):
    """Simple input model for testing."""

    message: str


async def _execute_implicit(
    broker: ToolBroker,
    tool_name: str,
    parameters: dict,
):
    """Exercise the sole prepare/authorize/execute broker interface."""
    prepared = broker.prepare(tool_name, parameters)
    mode = prepared.definition.permission.resolve(prepared.typed_input)
    if mode is not PermissionMode.IMPLICIT:
        raise ToolBrokerError(f"Tool {tool_name} requires authorization")
    return await broker.execute_prepared(
        prepared,
        AuthorizationContext(mode.value, "registry-v1", "test"),
    )


def _setup_broker(audit_fn=None):
    """Create a broker with a registered echo tool."""
    registry = ToolRegistry()
    tool = ToolDefinition(
        name="echo",
        description="Echoes input",
        input_model=_EchoInput,
        permission=PermissionPolicy(mode=PermissionMode.IMPLICIT),
    )
    registry.register(tool)
    broker = ToolBroker(registry=registry, audit_fn=audit_fn)

    async def echo_handler(validated):
        return {"echo": validated.message}

    broker.bind_handler("echo", echo_handler)
    broker.finalize()
    return broker


class TestExecute:
    """Tests for tool execution."""

    async def test_successful_execution(self):
        """Verify tool executes and returns result."""
        broker = _setup_broker()
        result = await _execute_implicit(broker, "echo", {"message": "hello"})
        assert result.success
        assert result.result == {"echo": "hello"}
        assert result.authorization.permission_mode == "implicit"
        assert result.duration_ms >= 0

    async def test_unknown_tool(self):
        """Verify unknown tool raises error."""
        broker = _setup_broker()
        with pytest.raises(ToolBrokerError, match="Unknown"):
            await _execute_implicit(broker, "nonexistent", {})

    async def test_direct_execution_cannot_bypass_required_authorization(self):
        """Non-implicit tools require an explicit AuthorizationContext."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="protected",
                description="Protected tool",
                input_model=_EchoInput,
                permission=PermissionPolicy(
                    mode=PermissionMode.CONVERSATION_CAPABILITY
                ),
            )
        )
        broker = ToolBroker(registry=registry)
        broker.bind_handler("protected", AsyncMock(return_value={"ok": True}))
        broker.finalize()

        with pytest.raises(ToolBrokerError, match="requires authorization"):
            await _execute_implicit(broker, "protected", {"message": "hello"})

    def test_prepared_input_is_immutable(self):
        """Policy and execution share a frozen typed input snapshot."""
        broker = _setup_broker()
        prepared = broker.prepare("echo", {"message": "hello"})

        with pytest.raises(ValidationError, match="frozen_instance"):
            prepared.typed_input.message = "changed"
        with pytest.raises(TypeError):
            prepared.canonical_parameters["message"] = "changed"

    def test_persisted_prepared_snapshot_requires_exact_hash_and_revision(self):
        """A paused invocation resumes only from its exact canonical identity."""
        broker = _setup_broker()
        original = broker.prepare("echo", {"message": "hello"})

        restored = broker.restore_prepared(
            "echo",
            dict(original.canonical_parameters),
            expected_hash=original.canonical_hash,
            expected_policy_revision="registry-v1",
        )

        assert restored.canonical_json == original.canonical_json
        with pytest.raises(ToolBrokerError, match="hash mismatch"):
            broker.restore_prepared(
                "echo",
                {"message": "changed"},
                expected_hash=original.canonical_hash,
                expected_policy_revision="registry-v1",
            )
        with pytest.raises(ToolBrokerError, match="revision mismatch"):
            broker.restore_prepared(
                "echo",
                dict(original.canonical_parameters),
                expected_hash=original.canonical_hash,
                expected_policy_revision="obsolete",
            )

    async def test_validation_failure(self):
        """Verify invalid params raise error."""
        broker = _setup_broker()
        with pytest.raises(ToolBrokerError, match="Validation"):
            await _execute_implicit(broker, "echo", {"bad": "param"})

    async def test_no_handler(self):
        """Verify missing handler raises error."""
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="no_handler",
            description="No handler",
            input_model=_EchoInput,
            permission=PermissionPolicy(mode=PermissionMode.IMPLICIT),
        )
        registry.register(tool)
        broker = ToolBroker(registry=registry)

        with pytest.raises(RuntimeError, match="missing handlers"):
            broker.finalize()

    def test_scheduler_authorization_pins_policy_revision(self):
        """Scheduler selection is validated and versioned by the broker."""
        broker = _setup_broker()

        assert broker.scheduler_authorizations(["echo"]) == [
            {
                "tool_name": "echo",
                "approved_policy_revision": "registry-v1",
            }
        ]

    def test_scheduler_authorization_rejects_duplicate_tool(self):
        """Duplicate scheduler selections fail closed instead of being normalized."""
        broker = _setup_broker()

        with pytest.raises(ToolBrokerError, match="Duplicate scheduled tool"):
            broker.scheduler_authorizations(["echo", "echo"])

    def test_scheduler_authorization_rejects_unknown_tool(self):
        """Unknown scheduler capabilities fail closed."""
        broker = _setup_broker()

        with pytest.raises(ToolBrokerError, match="Unknown scheduled tool"):
            broker.scheduler_authorizations(["missing"])


class TestRateLimiting:
    """Tests for rate limit enforcement in broker."""

    async def test_rate_limit_blocks(self):
        """Verify rate-limited calls are blocked."""
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="limited",
            description="Limited tool",
            input_model=_EchoInput,
            permission=PermissionPolicy(mode=PermissionMode.IMPLICIT),
        )
        registry.register(tool)

        limiter = ToolRateLimiter()
        # Exhaust the unknown tool limit (1)
        limiter.record("limited")
        broker = ToolBroker(registry=registry, rate_limiter=limiter)
        broker.bind_handler("limited", AsyncMock())
        broker.finalize()

        with pytest.raises(ToolBrokerError, match="Rate limit"):
            await _execute_implicit(broker, "limited", {"message": "test"})


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

        await _execute_implicit(broker, "echo", {"message": "hello"})
        await asyncio.sleep(0)

        audit_fn.assert_awaited_once()
        call_args = audit_fn.await_args
        assert call_args.args == ("tool_success", "echo")
        assert call_args.kwargs["zone"] is None

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
            await _execute_implicit(broker, "nonexistent", {})
        await asyncio.sleep(0)

        audit_fn.assert_awaited_once()
        call_args = audit_fn.await_args
        assert call_args.args == ("tool_rejected", "nonexistent")

    async def test_missing_audit_fn_skips_audit(self):
        """Verify missing audit callback skips audit without error."""
        broker = _setup_broker(audit_fn=None)

        result = await _execute_implicit(broker, "echo", {"message": "hello"})
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
            permission=PermissionPolicy(mode=PermissionMode.IMPLICIT),
        )
        registry.register(tool)
        broker = ToolBroker(registry=registry, audit_fn=audit_fn)

        async def leaky_handler(validated):
            return {"secret": fake_key}

        broker.bind_handler("leaky", leaky_handler)
        broker.finalize()
        await _execute_implicit(broker, "leaky", {"message": "go"})
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
            permission=PermissionPolicy(mode=PermissionMode.IMPLICIT),
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

        broker.bind_handler("limited", handler)
        broker.finalize()

        with pytest.raises(ToolBrokerError, match="Rate limit"):
            await _execute_implicit(broker, "limited", {"message": "blocked"})

        broker.reset_rate_limit("limited")

        result = await _execute_implicit(broker, "limited", {"message": "unblocked"})
        assert result.success
        assert result.result == {
            "echo": "unblocked",
        }
