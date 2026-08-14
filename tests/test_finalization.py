"""Tool-use-reask requirement/decision tests (R1-R3)."""


from backend.agent.finalization import (
    FinalAnswerDecision,
    RequirementSatisfaction,
    RequirementStatus,
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


def test_requirement_satisfaction_non_satisfaction_states() -> None:
    """RequirementSatisfaction tracks failed and pending non-satisfaction states."""
    rs = RequirementSatisfaction()
    assert rs.failed_tools == {}
    assert rs.invocations == ()
    rs2 = rs.with_failure("http_fetch", "connection timeout")
    assert rs2.failed_tools == {"http_fetch": "connection timeout"}
    assert rs2.satisfied_tools == frozenset()
    rs3 = rs2.add_invocation("url:1", "https://example.com")
    inv = rs3.invocations[0]
    assert inv.key == "url:1"
    assert inv.status == RequirementStatus.PENDING
    rs4 = rs3.with_invocation_status("url:1", RequirementStatus.FAILED, "DNS error")
    assert rs4.invocations[0].status == RequirementStatus.FAILED
    assert rs4.invocations[0].error == "DNS error"
    assert rs.invocations == ()
    assert rs.failed_tools == {}
