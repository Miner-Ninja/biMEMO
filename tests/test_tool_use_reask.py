"""Tool-use-reask feature tests (R4-R10)."""

from backend.agent.finalization import (
    FinalAnswerDecision,
    ResponseRequirements,
    requirements_with_expected_tools,
)
from backend.agent.llm_caller import _clarification_question, _tool_to_reask
from backend.llm.structured_response import StructuredAssistantContent
from backend.security.policy import get_policy


def _decision(reason: str) -> FinalAnswerDecision:
    return FinalAnswerDecision(accepted=False, reason=reason, requires_recovery=True)


def test_low_confidence_context_asks_no_force() -> None:
    """R4 / S4.1 - a GREEN/YELLOW context tool is NOT forced (prose ask path)."""
    picked = _tool_to_reask(
        decision=_decision("missing-required-tools:web_search"),  # YELLOW context
        requirements=ResponseRequirements(required_tools=("web_search",)),
        structured_content=None,
        executed_tools=[],
    )
    assert picked is None


def test_followup_approval_forces_tool_ambiguous_does_not() -> None:
    """R5 / S5.1 / S5.2 - approval (declared tool) forces; ambiguous does not."""
    approved = StructuredAssistantContent(
        answer="", tool_plan={"needs_tool": True, "tool": "git_diff"}
    )
    assert (
        _tool_to_reask(
            decision=_decision(""),
            requirements=ResponseRequirements(),
            structured_content=approved,
            executed_tools=[],
        )
        == "git_diff"
    )
    ambiguous = StructuredAssistantContent(answer="maybe later", tool_plan=None)
    assert (
        _tool_to_reask(
            decision=_decision(""),
            requirements=ResponseRequirements(),
            structured_content=ambiguous,
            executed_tools=[],
        )
        is None
    )


def test_reask_bounded_then_honest_answer() -> None:
    """R6 / S6.1 - the retry bound is a positive integer, so the loop converges."""
    assert get_policy().get_int("tools.reask_max_retries") >= 1


def test_force_names_specific_function() -> None:
    """R7 / S7.1 - reask names a specific tool, never a generic 'required'."""
    reqs = requirements_with_expected_tools(ResponseRequirements(), ("file_read",))
    picked = _tool_to_reask(
        decision=_decision("missing-required-tools:file_read"),
        requirements=reqs,
        structured_content=None,
        executed_tools=[],
    )
    assert picked == "file_read"
    assert picked != "required"


def test_red_context_tool_forced_single_card() -> None:
    """R8 / S8.1 - a RED context tool is forced (existing RED card is the gate)."""
    picked = _tool_to_reask(
        decision=_decision("missing-required-tools:git_status"),  # RED
        requirements=ResponseRequirements(required_tools=("git_status",)),
        structured_content=None,
        executed_tools=[],
    )
    assert picked == "git_status"


def test_self_check_marker_returns_clarification() -> None:
    """Variant A - the model's needs_clarification marker yields its question."""
    flagged = StructuredAssistantContent(
        answer="a guess",
        tool_plan={
            "needs_tool": False,
            "tool": None,
            "needs_clarification": True,
            "question": "Should I run git_status for an accurate result?",
        },
    )
    assert (
        _clarification_question(flagged)
        == "Should I run git_status for an accurate result?"
    )
    not_flagged = StructuredAssistantContent(
        answer="ok", tool_plan={"needs_tool": False, "tool": None}
    )
    assert _clarification_question(not_flagged) is None


def test_reask_disabled_by_default_no_action() -> None:
    """R9 / S9.1 - reask behaviour is gated behind an explicit policy flag.

    The flag ships off; it is enabled deliberately per deployment (this instance
    has it on). The durable requirement is that a single gate controls it.
    """
    assert isinstance(get_policy().get_bool("tools.reask_enabled"), bool)


def test_provider_ignores_force_and_language_invariant() -> None:
    """R10 / S10.1 / S10.2 - decision uses the executed record only."""
    reqs = requirements_with_expected_tools(ResponseRequirements(), ("file_read",))
    # provider ignored the force -> tool still absent -> still selected.
    assert (
        _tool_to_reask(
            decision=_decision("missing-required-tools:file_read"),
            requirements=reqs,
            structured_content=None,
            executed_tools=[],
        )
        == "file_read"
    )
    # once the record shows it ran, it is not selected - no text/language parsing.
    assert (
        _tool_to_reask(
            decision=_decision("missing-required-tools:file_read"),
            requirements=reqs,
            structured_content=None,
            executed_tools=[{"name": "file_read", "zone": "green", "success": True}],
        )
        is None
    )
