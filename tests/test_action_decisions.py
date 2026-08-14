"""Tests for assistant action decision checks."""

from backend.permissions import (
    ActionContext,
    ActionDecisionService,
    DecisionKind,
)
from backend.tools.broker import ToolBroker
from backend.tools.handlers.registration import register_tool_definitions
from backend.tools.registry import ToolRegistry


def _service_and_broker() -> tuple[ActionDecisionService, ToolBroker]:
    """Build one finalized registry and its policy service."""
    registry = ToolRegistry()
    register_tool_definitions(registry)
    broker = ToolBroker(registry=registry, workspace_roots_fn=lambda: ["/tmp"])
    for name in registry.tool_names:
        broker.bind_handler(name, lambda _input: None)
    broker.finalize()
    return (
        ActionDecisionService(registry=registry, workspace_roots=lambda: ["/tmp"]),
        broker,
    )


def _context(tool_name: str, parameters: dict) -> ActionContext:
    """Build an action context for one tool call."""
    _service, broker = _service_and_broker()
    return ActionContext(
        conversation_id="conv-1",
        channel="web",
        session_id="session-1",
        tool_name=tool_name,
        parameters=parameters,
        prepared=broker.prepare(tool_name, parameters),
    )


class TestActionDecisionService:
    """Focused tests for non-negotiable action blocks."""

    def test_allows_normal_file_read(self):
        """Allow ordinary file reads."""
        service, _broker = _service_and_broker()
        decision = service.decide(_context("file_read", {"path": "/tmp/readme.md"}))
        assert decision.kind == DecisionKind.ASK_SCOPED

    def test_blocks_private_key_read(self):
        """Block private-key path reads."""
        service, _broker = _service_and_broker()
        decision = service.decide(
            _context("file_read", {"path": "/home/user/.ssh/id_rsa"})
        )
        assert decision.kind == DecisionKind.BLOCK
        assert "sensitive" in decision.reason.lower()
        assert decision.details["source"] == "hard_safety"
        assert decision.details["permission_mode"] == "blocked"
        assert decision.details["policy_revision"] == "registry-v1"

    def test_blocks_path_traversal(self):
        """Block path traversal attempts."""
        service, _broker = _service_and_broker()
        decision = service.decide(_context("file_read", {"path": "../secret.txt"}))
        assert decision.kind == DecisionKind.BLOCK
        assert "outside every allowed workspace" in decision.reason.lower()

    def test_blocks_cloud_metadata_fetch(self):
        """Block cloud metadata fetches."""
        service, _broker = _service_and_broker()
        decision = service.decide(
            _context("http_fetch", {"url": "http://169.254.169.254/latest/meta-data"})
        )
        assert decision.kind == DecisionKind.BLOCK

    def test_blocks_destructive_project_command(self):
        """Block destructive project commands."""
        service, _broker = _service_and_broker()
        decision = service.decide(
            _context(
                "execute_command",
                {"command": "rm -rf /", "cwd": "/tmp", "timeout": 30},
            )
        )
        assert decision.kind == DecisionKind.BLOCK
        assert "destructive" in decision.reason.lower()

    def test_red_zone_without_grant_asks_once(self):
        """Ask once when a red-zone call has no saved grant."""
        service, _broker = _service_and_broker()
        context = _context(
            "email_send", {"to": "a@b.test", "subject": "x", "body": "y"}
        )
        decision = service.decide(context)

        assert decision.kind == DecisionKind.ASK_ONCE

    def test_unsupported_policy_version_is_blocked(self):
        """Never route an invocation through an old policy implementation."""
        service, _broker = _service_and_broker()
        context = _context(
            "email_send", {"to": "a@b.test", "subject": "x", "body": "y"}
        )
        context = ActionContext(**{**context.__dict__, "tool_policy_version": "old"})

        decision = service.decide(context)

        assert decision.kind == DecisionKind.BLOCK
