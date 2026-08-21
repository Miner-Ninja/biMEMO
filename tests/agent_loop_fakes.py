"""Shared fakes and helpers for agent loop tests."""

from unittest.mock import MagicMock

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.concurrency import AgentConcurrency
from backend.llm.call_factory import LLMResponse
from backend.permissions import ActionDecision, DecisionKind


def _text_response(text: str) -> LLMResponse:
    """Create an LLMResponse with plain text (no tool calls)."""
    return LLMResponse(
        text=text,
        tool_calls=[],
        raw_message={"role": "assistant", "content": text},
    )


def _make_orchestrator(
    broker: object | None = None,
    context: list | None = None,
) -> AgentOrchestrator:
    """Create an orchestrator with test defaults."""
    if broker is None:
        broker = MagicMock()
    ctx = context if context is not None else []
    orchestrator = AgentOrchestrator(
        broker=broker,
        concurrency=AgentConcurrency(),
        context_fn=lambda sid, **kwargs: ctx,
    )
    if not hasattr(broker, "finalized") or isinstance(broker, MagicMock):
        orchestrator.tool_executor.action_decisions.decide = MagicMock(
            side_effect=lambda context: (
                ActionDecision(
                    DecisionKind.ASK_ONCE,
                    details={
                        "permission_mode": "per_invocation",
                        "policy_revision": "registry-v1",
                    },
                )
                if context.tool_name == "email_send"
                else ActionDecision(
                    DecisionKind.ALLOW,
                    details={
                        "permission_mode": "implicit",
                        "policy_revision": "registry-v1",
                    },
                )
            )
        )
    return orchestrator
