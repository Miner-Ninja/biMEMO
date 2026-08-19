"""LLM invocation delegate for the gateway orchestrator.

Handles all LLM calls including SSE streaming, tool choice overrides,
and the main model/tool loop.
Delegates tool execution to the ToolExecutor and uses helper functions
for LLM capability detection.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    llm_accepts_call_options,
    llm_accepts_sse_queue,
    llm_accepts_tools_override,
    request_missing_suggestions,
    request_non_empty_final_answer,
)
from backend.agent.agent_loop_models import ModelLoopResult
from backend.agent.finalization import (
    FinalAnswerDecision,
    RequirementSatisfaction,
    RequirementStatus,
    ResponseRequirements,
    build_required_tool_failure_answer,
    validate_final_answer,
)
from backend.agent.loop import (
    AgenticLoop,
    LoopState,
)
from backend.llm.call_options import EventSink, LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat
from backend.security.policy import get_policy


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.tool_executor import ToolExecutor
    from backend.llm.usage import LLMCallUsage
    from backend.tools.broker import ToolBroker


logger = logging.getLogger(__name__)


class LLMStreamMode(StrEnum):
    """Controls which LLM stream events may reach the UI."""

    ALL = "all"
    THINKING_ONLY = "thinking_only"
    DISABLED = "disabled"


class ThinkingOnlyStreamQueue:
    """Forward only model reasoning events while buffering visible answer text."""

    def __init__(self, target_queue: object | None) -> None:
        """Initialize the queue wrapper."""
        self.target_queue = target_queue

    async def put(self, item: object) -> None:
        """Forward reasoning events to the wrapped queue."""
        if not isinstance(item, dict):
            return
        if item.get("type") != "thinking" or self.target_queue is None:
            return
        await self.target_queue.put(item)


CLARIFICATION_APPROVAL_PROMPT = (
    "Classify whether the user's reply clearly approves the proposed tool action. "
    "Answer with exactly YES or NO. YES requires clear approval. Ambiguous, "
    "unrelated, conditional, or negative replies are NO. Do not explain."
)


def _tool_to_reask(
    *,
    decision: FinalAnswerDecision,
    requirements: ResponseRequirements,
    structured_content: object,
    executed_tools: list[dict],
    available_tools: set[str] | None = None,
) -> str | None:
    """Pick the single tool to force on reask, or None to force nothing.

    A user-forced or model-declared tool (``must_use_tools`` / ``tool_plan``)
    is retried when it is still required and available. Permission is resolved
    later from the frozen registry; this selection step has no security role.
    """
    executed = {
        str(tool.get("name"))
        for tool in executed_tools
        if tool.get("success") is True or tool.get("status") == "succeeded"
    }
    # L2 — a model-declared tool that did not run forces in any zone.
    tool_plan = getattr(structured_content, "tool_plan", None)
    if isinstance(tool_plan, dict) and tool_plan.get("needs_tool"):
        declared = tool_plan.get("tool")
        if (
            declared
            and str(declared) not in executed
            and (available_tools is None or str(declared) in available_tools)
        ):
            return str(declared)
    # L1 / context — missing required tools reported by the finalization gate.
    if not decision.reason.startswith("missing-required-tools:"):
        return None
    missing = [
        name
        for name in decision.reason[len("missing-required-tools:") :].split(",")
        if name and name not in executed
    ]
    return next(
        (
            name
            for name in missing
            if available_tools is None or name in available_tools
        ),
        None,
    )


def _clarification_question(structured_content: object) -> str | None:
    """Return the model's self-check clarifying question, if it flagged one.

    The model sets ``tool_plan.needs_clarification`` and puts a short question in
    ``tool_plan.question`` when it is unsure whether a tool/action is needed. This
    is a hint only — its reliability is bounded by the model's self-calibration.
    """
    tool_plan = getattr(structured_content, "tool_plan", None)
    if not isinstance(tool_plan, dict) or not tool_plan.get("needs_clarification"):
        return None
    question = tool_plan.get("question")
    text = str(question).strip() if question else ""
    return text or None


class LLMCaller:
    """Handle model invocations for the structured agent loop.

    Attributes:
        model_name: Name of the current LLM model.
        broker: Tool broker for forced tool execution.
    """

    def __init__(
        self,
        model_name: str,
        broker: ToolBroker,
    ) -> None:
        """Initialize LLM caller.

        Args:
            model_name: Name of the current LLM model.
            broker: Tool broker for forced tool execution.
        """
        self.model_name = model_name
        self.broker = broker
        self.sse_queue: object | None = None
        self.on_attempt: Callable | None = None

    async def call_once(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        call_usages: list[LLMCallUsage],
        tool_choice: object | None = None,
        stream_mode: LLMStreamMode = LLMStreamMode.ALL,
        phase: str = "Stage 1",
        thinking_enabled: bool | None = None,
        response_format: LlmResponseFormat | None = None,
    ) -> object:
        """Call the LLM once with the currently prepared prompt messages."""
        llm_response = await self.invoke(
            llm_call=llm_call,
            messages=messages,
            openai_tools=openai_tools,
            tool_choice=tool_choice,
            stream_mode=stream_mode,
            phase=phase,
            thinking_enabled=thinking_enabled,
            response_format=response_format,
        )
        if llm_response.usage is not None:
            call_usages.append(llm_response.usage)
        return llm_response

    async def invoke(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        stream_mode: LLMStreamMode = LLMStreamMode.ALL,
        tool_choice: object | None = None,
        phase: str = "Stage 1",
        thinking_enabled: bool | None = None,
        response_format: LlmResponseFormat | None = None,
        response_schema: dict | None = None,
    ) -> object:
        """Invoke the configured LLM with the supported optional arguments."""
        options = LLMCallOptions(
            response_format=(
                response_format
                if response_format is not None
                else LlmResponseFormat.TEXT
                if openai_tools
                else None
            ),
            response_schema=response_schema,
            tools=openai_tools,
            tool_choice=tool_choice,
            sse_queue=self._stream_queue_for_mode(stream_mode),
            event_sink=(
                EventSink.DISABLED if stream_mode is LLMStreamMode.DISABLED else None
            ),
            on_attempt=self.on_attempt,
            phase=phase,
            thinking_enabled=thinking_enabled,
        )
        if llm_accepts_call_options(llm_call):
            return await llm_call(messages, options=options)
        if stream_mode is not LLMStreamMode.DISABLED and llm_accepts_sse_queue(
            llm_call
        ):
            return await self.invoke_with_sse(
                llm_call=llm_call,
                messages=messages,
                openai_tools=openai_tools,
                tool_choice=tool_choice,
                stream_mode=stream_mode,
            )
        if llm_accepts_tools_override(llm_call):
            kwargs: dict[str, object] = {"tools_override": openai_tools}
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            return await llm_call(messages, **kwargs)
        return await llm_call(messages)

    async def invoke_with_sse(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        tool_choice: object | None = None,
        stream_mode: LLMStreamMode = LLMStreamMode.ALL,
    ) -> object:
        """Invoke the configured LLM while forwarding tokens into the SSE queue."""
        stream_queue = self._stream_queue_for_mode(stream_mode)
        if llm_accepts_tools_override(llm_call):
            kwargs: dict[str, object] = {
                "sse_queue_override": stream_queue,
                "tools_override": openai_tools,
            }
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            return await llm_call(messages, **kwargs)
        return await llm_call(
            messages,
            stream_queue,
        )

    def _stream_queue_for_mode(self, stream_mode: LLMStreamMode) -> object | None:
        """Return the queue object that implements the requested stream policy."""
        if stream_mode is LLMStreamMode.ALL:
            return self.sse_queue
        if stream_mode is LLMStreamMode.THINKING_ONLY:
            return ThinkingOnlyStreamQueue(self.sse_queue)
        return None

    async def classify_clarification_approval(
        self,
        *,
        reply: str,
        proposed_tool: str,
        llm_call: object,
        call_usages: list[LLMCallUsage],
    ) -> bool:
        """Conservatively classify one follow-up reply without exposing tools."""
        response = await self.invoke(
            llm_call=llm_call,
            messages=[
                {"role": "system", "content": CLARIFICATION_APPROVAL_PROMPT},
                {
                    "role": "user",
                    "content": (f"Proposed tool: {proposed_tool}\nUser reply: {reply}"),
                },
            ],
            openai_tools=[],
            stream_mode=LLMStreamMode.DISABLED,
            phase="Clarification classifier",
        )
        if response.usage is not None:
            call_usages.append(response.usage)
        return response.text.strip().casefold() == "yes"

    async def run_model_loop(
        self,
        *,
        loop: AgenticLoop,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        call_usages: list[LLMCallUsage],
        channel: str,
        tool_executor: ToolExecutor,
        force_web_search: bool = False,
        forced_tool_choice: dict[str, object] | str | None = None,
        requirements: ResponseRequirements | None = None,
        satisfaction: RequirementSatisfaction | None = None,
        suggestions_enabled: bool = True,
        thinking_enabled: bool | None = None,
        response_format: LlmResponseFormat | None = None,
    ) -> ModelLoopResult:
        """Run the model/tool loop until a final answer is available."""
        raw_final_text = ""
        structured_content = None
        executed_immediate_tools = False
        requirements = requirements or ResponseRequirements()
        satisfaction = satisfaction or RequirementSatisfaction()
        available_tools = {
            str(tool.get("function", {}).get("name", ""))
            for tool in openai_tools or []
            if isinstance(tool.get("function"), dict)
        }
        has_prefetch_failure = any(
            tool in satisfaction.failed_tools for tool in requirements.required_tools
        ) or any(
            invocation.status is RequirementStatus.FAILED
            for invocation in satisfaction.invocations
        )
        if has_prefetch_failure:
            return ModelLoopResult(
                raw_final_text=build_required_tool_failure_answer(
                    requirements=requirements,
                    satisfaction=satisfaction,
                )
            )

        reask_count = 0
        reask_forced_tool: dict[str, object] | None = None
        decision = FinalAnswerDecision(accepted=False, reason="")
        if get_policy().get_bool("tools.reask_enabled"):
            retries_per_tool = get_policy().get_int("tools.reask_max_retries")
            tool_count = max(1, len(requirements.must_use_tools))
            max_reasks = tool_count * (retries_per_tool + 1) - 1
        else:
            max_reasks = 0
        for _ in range(max_reasks + 1):
            first_iteration = True
            while True:
                state = loop.check_limits()
                if state != LoopState.RUNNING:
                    break

                forced_choice = None
                if first_iteration and force_web_search:
                    forced_choice = {
                        "type": "function",
                        "function": {"name": "web_search"},
                    }
                elif first_iteration and reask_forced_tool is not None:
                    forced_choice = reask_forced_tool
                elif first_iteration and forced_tool_choice is not None:
                    forced_choice = forced_tool_choice

                llm_response = await self.call_once(
                    llm_call=llm_call,
                    messages=messages,
                    openai_tools=openai_tools,
                    call_usages=call_usages,
                    tool_choice=forced_choice,
                    stream_mode=(
                        LLMStreamMode.THINKING_ONLY
                        if requirements.required_tools
                        else LLMStreamMode.ALL
                    ),
                    thinking_enabled=thinking_enabled,
                    response_format=response_format,
                )
                first_iteration = False
                round_result = await tool_executor.run_one_tool_round(
                    llm_response=llm_response,
                    messages=messages,
                    channel=channel,
                    required_tools=requirements.required_tools,
                )
                if round_result.raw_final_text:
                    raw_final_text = round_result.raw_final_text
                    structured_content = round_result.structured_content
                    break
                if round_result.executed_immediate_tools:
                    executed_immediate_tools = True
                if not round_result.should_continue:
                    break

                loop.advance()

            pending_red_calls = getattr(tool_executor, "pending_red_calls", [])
            if isinstance(pending_red_calls, list) and pending_red_calls:
                return ModelLoopResult(
                    raw_final_text=(
                        "A required tool call is waiting for confirmation. "
                        "No final answer has been accepted yet."
                    )
                )

            satisfaction = satisfaction.with_execution_records(
                tool_executor.executed_tools
            )

            if (
                requirements.needs_current_data
                and satisfaction.missing_current_data_targets
            ):
                covered = self._current_data_targets_covered_by_tools(
                    messages, satisfaction.missing_current_data_targets
                )
                if covered:
                    satisfaction = satisfaction.with_current_data_targets_covered(
                        covered
                    )

            decision = validate_final_answer(
                raw_final_text=raw_final_text,
                structured_content=structured_content,
                requirements=requirements,
                satisfaction=satisfaction,
            )
            # Reask: force a missing tool back through the loop when enabled.
            # Forced (must_use) and model-declared tools are forced in ANY zone;
            # a context-only tool is forced only when it is RED. GREEN/YELLOW
            # context tools fall through to the prose ask + next-turn tool_plan.
            if get_policy().get_bool("tools.reask_enabled"):
                force_tool = _tool_to_reask(
                    decision=decision,
                    requirements=requirements,
                    structured_content=structured_content,
                    executed_tools=tool_executor.executed_tools,
                    available_tools=available_tools,
                )
                logger.info(
                    "reask check: required=%s must_use=%s executed=%s "
                    "declared=%s reason=%r -> force=%s (attempt %d/%d)",
                    list(requirements.required_tools),
                    list(requirements.must_use_tools),
                    [t.get("name") for t in tool_executor.executed_tools],
                    getattr(structured_content, "tool_plan", None),
                    decision.reason,
                    force_tool,
                    reask_count,
                    max_reasks,
                )
                if force_tool is not None:
                    if reask_count < max_reasks:
                        reask_count += 1
                        reask_forced_tool = {
                            "type": "function",
                            "function": {"name": force_tool},
                        }
                        continue
                    return ModelLoopResult(
                        raw_final_text=build_required_tool_failure_answer(
                            requirements=requirements,
                            satisfaction=satisfaction,
                        )
                    )

            if not decision.accepted and not decision.requires_recovery:
                return ModelLoopResult(
                    raw_final_text=build_required_tool_failure_answer(
                        requirements=requirements,
                        satisfaction=satisfaction,
                    )
                )

            if decision.requires_recovery or (
                executed_immediate_tools and not raw_final_text.strip()
            ):
                recovered_response = await request_non_empty_final_answer(
                    llm_call=llm_call,
                    messages=messages,
                    call_usages=call_usages,
                )
                if recovered_response is not None and recovered_response.answer.strip():
                    raw_final_text = recovered_response.answer
                    structured_content = recovered_response
                    decision = validate_final_answer(
                        raw_final_text=raw_final_text,
                        structured_content=structured_content,
                        requirements=requirements,
                        satisfaction=satisfaction,
                    )

            break

        # Self-check marker (variant A): when the model flagged uncertainty, show
        # its clarifying question instead of a guessed answer. A hint on top of
        # the deterministic reask; accuracy is bounded by the model's calibration.
        if get_policy().get_bool("tools.reask_enabled"):
            clarification = _clarification_question(structured_content)
            if clarification:
                raw_final_text = clarification
                if structured_content is not None:
                    structured_content = replace(
                        structured_content, answer=clarification
                    )
                decision = FinalAnswerDecision(accepted=True, reason="clarification")

        if raw_final_text.strip():
            if not decision.accepted:
                logger.warning(
                    "Final answer rejected after recovery: %s",
                    decision.reason,
                )
                return ModelLoopResult(
                    raw_final_text=(
                        "I could not produce a complete final answer for that "
                        "request. Please try again."
                    ),
                )
            if (
                suggestions_enabled
                and structured_content is not None
                and len(structured_content.suggestions) < 3
            ):
                try:
                    suggestions = await request_missing_suggestions(
                        llm_call=llm_call,
                        messages=messages,
                        answer_text=structured_content.answer or raw_final_text,
                        call_usages=call_usages,
                    )
                except Exception:
                    logger.warning(
                        "Missing suggestions recovery failed",
                        exc_info=True,
                    )
                    suggestions = []
                if suggestions:
                    merged_suggestions = [
                        *structured_content.suggestions,
                        *[
                            suggestion
                            for suggestion in suggestions
                            if suggestion not in structured_content.suggestions
                        ],
                    ][:3]
                    structured_content = replace(
                        structured_content,
                        suggestions=merged_suggestions,
                    )
            return ModelLoopResult(
                raw_final_text=raw_final_text,
                structured_content=structured_content,
            )
        return ModelLoopResult(
            raw_final_text=(
                "I could not produce a final answer for that request. Please try again."
            ),
        )
