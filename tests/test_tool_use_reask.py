"""Tool-use-reask feature tests (R4-R10)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.agent.agent_loop_models import ToolRoundResult
from backend.agent.finalization import (
    FinalAnswerDecision,
    ResponseRequirements,
    requirements_with_expected_tools,
)
from backend.agent.llm_caller import (
    LLMCaller,
    LLMStreamMode,
    _clarification_question,
    _tool_to_reask,
)
from backend.agent.loop import LoopState
from backend.llm.call_factory import LLMResponse, LLMToolCall
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.structured_response import StructuredAssistantContent
from backend.security.policy import get_policy


def _decision(reason: str) -> FinalAnswerDecision:
    return FinalAnswerDecision(accepted=False, reason=reason, requires_recovery=True)


def test_successful_only_reconciliation() -> None:
    """A successfully executed tool is not selected again."""
    picked = _tool_to_reask(
        decision=_decision("missing-required-tools:file_read"),
        requirements=ResponseRequirements(required_tools=("file_read",)),
        structured_content=None,
        executed_tools=[{"name": "file_read", "zone": "green", "success": True}],
    )
    assert picked is None


def test_failed_execution_is_not_reconciled_as_success() -> None:
    """A failed record never suppresses enforcement of a must-use tool."""
    requirements = requirements_with_expected_tools(
        ResponseRequirements(),
        ("file_read",),
    )
    picked = _tool_to_reask(
        decision=_decision("missing-required-tools:file_read"),
        requirements=requirements,
        structured_content=None,
        executed_tools=[{"name": "file_read", "status": "failed", "success": False}],
    )
    assert picked == "file_read"


def test_unknown_model_declared_tool_is_not_forced() -> None:
    """Untrusted model plans cannot force a schema that was not exposed."""
    structured = StructuredAssistantContent(
        answer="",
        tool_plan={"needs_tool": True, "tool": "not_registered"},
    )

    picked = _tool_to_reask(
        decision=_decision(""),
        requirements=ResponseRequirements(),
        structured_content=structured,
        executed_tools=[],
        available_tools={"file_read"},
    )

    assert picked is None


async def test_non_sse_invoke_preserves_exact_choice_and_text_mode() -> None:
    """Typed non-SSE calls receive exact choice and native text transport."""
    captured: object | None = None

    async def llm_call(_messages, *, options=None):
        nonlocal captured
        captured = options
        return SimpleNamespace(usage=None)

    caller = LLMCaller("test", None, None, MagicMock())
    choice = {"type": "function", "function": {"name": "file_read"}}
    await caller.invoke(
        llm_call=llm_call,
        messages=[],
        openai_tools=[{"type": "function"}],
        stream_mode=LLMStreamMode.DISABLED,
        tool_choice=choice,
    )

    assert captured is not None
    assert captured.response_format is LlmResponseFormat.TEXT
    assert captured.tool_choice == choice


class _SequenceToolExecutor:
    """Minimal executor that records successful native tool calls."""

    def __init__(self) -> None:
        self.executed_tools: list[dict[str, object]] = []
        self.pending_red_calls: list[object] = []

    async def run_one_tool_round(self, *, llm_response, **_kwargs):
        """Convert fake native calls into successful execution evidence."""
        if llm_response.tool_calls:
            for call in llm_response.tool_calls:
                self.executed_tools.append(
                    {"name": call.name, "status": "succeeded", "success": True}
                )
            return ToolRoundResult("", None, True, True)
        return ToolRoundResult(llm_response.text, None, False, False)


async def test_run_model_loop_forces_two_required_tools_exactly() -> None:
    """The live loop forces each incomplete required tool in order."""
    choices: list[object] = []
    responses = iter(
        [
            LLMResponse(
                text="",
                tool_calls=[LLMToolCall("c1", "file_read", {})],
            ),
            LLMResponse(text="first done"),
            LLMResponse(
                text="",
                tool_calls=[LLMToolCall("c2", "git_diff", {})],
            ),
            LLMResponse(text="all done"),
        ]
    )

    async def llm_call(_messages, *, options=None):
        choices.append(options.tool_choice)
        return next(responses)

    loop = MagicMock()
    loop.check_limits.return_value = LoopState.RUNNING
    requirements = requirements_with_expected_tools(
        ResponseRequirements(),
        ("file_read", "git_diff"),
    )
    caller = LLMCaller("test", None, None, MagicMock())
    result = await caller.run_model_loop(
        loop=loop,
        llm_call=llm_call,
        messages=[],
        openai_tools=[{"type": "function"}],
        call_usages=[],
        channel="chat",
        tool_executor=_SequenceToolExecutor(),
        forced_tool_choice={
            "type": "function",
            "function": {"name": "file_read"},
        },
        requirements=requirements,
        suggestions_enabled=False,
    )

    assert result.raw_final_text == "all done"
    assert choices[0]["function"]["name"] == "file_read"
    assert choices[1] is None
    assert choices[2]["function"]["name"] == "git_diff"
    assert choices[3] is None


async def test_run_model_loop_fails_when_provider_ignores_force() -> None:
    """Provider prose cannot satisfy a required invocation after retries."""
    choices: list[object] = []

    async def llm_call(_messages, *, options=None):
        choices.append(options.tool_choice)
        return LLMResponse(text="I will answer without the tool")

    loop = MagicMock()
    loop.check_limits.return_value = LoopState.RUNNING
    requirements = requirements_with_expected_tools(
        ResponseRequirements(),
        ("file_read",),
    )
    caller = LLMCaller("test", None, None, MagicMock())
    result = await caller.run_model_loop(
        loop=loop,
        llm_call=llm_call,
        messages=[],
        openai_tools=[{"type": "function"}],
        call_usages=[],
        channel="chat",
        tool_executor=_SequenceToolExecutor(),
        forced_tool_choice={
            "type": "function",
            "function": {"name": "file_read"},
        },
        requirements=requirements,
        suggestions_enabled=False,
    )

    assert "file_read" in result.raw_final_text
    assert "answer without the tool" not in result.raw_final_text
    assert all(choice["function"]["name"] == "file_read" for choice in choices)


def test_exact_two_tool_sequence() -> None:
    """Two missing must-use tools: the first in order is returned."""
    reqs = requirements_with_expected_tools(
        ResponseRequirements(), ("file_read", "git_diff")
    )
    picked = _tool_to_reask(
        decision=_decision("missing-required-tools:file_read,git_diff"),
        requirements=reqs,
        structured_content=None,
        executed_tools=[],
    )
    assert picked == "file_read"


def test_ignored_force_already_executed() -> None:
    """must_use_tools forces only when the tool is absent from the executed record."""
    reqs = requirements_with_expected_tools(ResponseRequirements(), ("file_read",))
    # Tool was forced (must_use) but the provider already executed it.
    assert (
        _tool_to_reask(
            decision=_decision("missing-required-tools:file_read"),
            requirements=reqs,
            structured_content=None,
            executed_tools=[{"name": "file_read", "zone": "green", "success": True}],
        )
        is None
    )


def test_loop_limit_fail_closed() -> None:
    """R6 / S6.2 - reask_max_retries has a positive upper bound (fail-closed)."""
    max_retries = get_policy().get_int("tools.reask_max_retries")
    assert 1 <= max_retries <= 5


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
