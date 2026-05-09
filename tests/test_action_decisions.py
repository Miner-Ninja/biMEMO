"""Tests for assistant action decision checks."""

from backend.permissions import (
    ActionContext,
    ActionDecisionService,
    DecisionKind,
)


def _context(tool_name: str, parameters: dict) -> ActionContext:
    return ActionContext(
        conversation_id="conv-1",
        channel="web",
        session_id="session-1",
        tool_name=tool_name,
        parameters=parameters,
    )


class TestActionDecisionService:
    """Focused tests for non-negotiable action blocks."""

    def test_allows_normal_file_read(self):
        service = ActionDecisionService()
        decision = service.decide(_context("file_read", {"path": "/tmp/readme.md"}))
        assert decision.kind == DecisionKind.ALLOW

    def test_blocks_private_key_read(self):
        service = ActionDecisionService()
        decision = service.decide(
            _context("file_read", {"path": "/home/user/.ssh/id_rsa"})
        )
        assert decision.kind == DecisionKind.BLOCK
        assert "sensitive" in decision.reason.lower()

    def test_blocks_path_traversal(self):
        service = ActionDecisionService()
        decision = service.decide(_context("file_read", {"path": "../secret.txt"}))
        assert decision.kind == DecisionKind.BLOCK
        assert "traversal" in decision.reason.lower()

    def test_blocks_cloud_metadata_fetch(self):
        service = ActionDecisionService()
        decision = service.decide(
            _context("http_fetch", {"url": "http://169.254.169.254/latest/meta-data"})
        )
        assert decision.kind == DecisionKind.BLOCK

    def test_blocks_destructive_project_command(self):
        service = ActionDecisionService()
        decision = service.decide(_context("run_command", {"command": "rm -rf /"}))
        assert decision.kind == DecisionKind.BLOCK
        assert "destructive" in decision.reason.lower()

    def test_red_zone_without_grant_asks_once(self):
        service = ActionDecisionService()
        context = _context("custom_tool", {})
        context = ActionContext(**{**context.__dict__, "trust_zone": "red"})

        decision = service.decide(context)

        assert decision.kind == DecisionKind.ASK_ONCE

    def test_saved_allow_grant_overrides_red_zone_prompt(self):
        class Grants:
            def find_decision(self, **kwargs):
                return "allow"

        service = ActionDecisionService(grants=Grants())
        context = _context("custom_tool", {})
        context = ActionContext(**{**context.__dict__, "trust_zone": "red"})

        decision = service.decide(context)

        assert decision.kind == DecisionKind.ALLOW
        assert decision.details["source"] == "grant"
