"""Tool-use-reask requirement/decision tests (R1-R3)."""

from backend.agent.finalization import (
    FinalAnswerDecision,
    ResponseRequirements,
    requirements_with_expected_tools,
)
from backend.agent.llm_caller import _tool_to_reask
from backend.llm.structured_response import StructuredAssistantContent


def _decision(reason: str) -> FinalAnswerDecision:
    return FinalAnswerDecision(accepted=False, reason=reason, requires_recovery=True)


def test_expected_vs_executed_uses_executed_record() -> None:
    """R1 / S1.1 / S1.2 - the decision reads the executed-tools record."""
    reqs = requirements_with_expected_tools(ResponseRequirements(), ("file_read",))
    # not executed -> selected for reask
    assert (
        _tool_to_reask(
            decision=_decision("missing-required-tools:file_read"),
            requirements=reqs,
            structured_content=None,
            executed_tools=[],
        )
        == "file_read"
    )
    # executed -> not selected (S1.2 accepts unchanged)
    assert (
        _tool_to_reask(
            decision=_decision("missing-required-tools:file_read"),
            requirements=reqs,
            structured_content=None,
            executed_tools=[{"name": "file_read", "zone": "green", "success": True}],
        )
        is None
    )


def test_forced_tool_missing_triggers_force_reask() -> None:
    """R2 / S2.1 - a user-forced tool is expected in any zone and forced."""
    reqs = requirements_with_expected_tools(ResponseRequirements(), ("file_read",))
    assert "file_read" in reqs.must_use_tools
    assert "file_read" in reqs.required_tools
    # GREEN forced tool is still forced because it is a must_use tool.
    assert (
        _tool_to_reask(
            decision=_decision("missing-required-tools:file_read"),
            requirements=reqs,
            structured_content=None,
            executed_tools=[],
        )
        == "file_read"
    )


def test_declared_tool_plan_missing_triggers_force_reask() -> None:
    """R3 / S3.1 / S3.2 - a model-declared tool that did not run is forced."""
    declared = StructuredAssistantContent(
        answer="", tool_plan={"needs_tool": True, "tool": "git_log"}
    )
    assert (
        _tool_to_reask(
            decision=_decision(""),
            requirements=ResponseRequirements(),
            structured_content=declared,
            executed_tools=[],
        )
        == "git_log"
    )
    none_declared = StructuredAssistantContent(
        answer="ok", tool_plan={"needs_tool": False, "tool": None}
    )
    assert (
        _tool_to_reask(
            decision=_decision(""),
            requirements=ResponseRequirements(),
            structured_content=none_declared,
            executed_tools=[],
        )
        is None
    )
