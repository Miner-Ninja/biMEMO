"""LLM invocation delegate for the gateway orchestrator.

Handles all LLM calls including SSE streaming, tool choice overrides,
forced web search classification, and the main model/tool loop.
Delegates tool execution to the ToolExecutor and uses helper functions
for LLM capability detection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    llm_accepts_sse_queue,
    llm_accepts_tools_override,
    llm_requires_forced_web_search,
    message_may_need_forced_web_search_classifier,
    message_may_need_memory_recall,
    message_requires_forced_web_search,
    request_non_empty_final_answer,
)
from backend.agent.agent_loop_models import ModelLoopResult
from backend.agent.loop import (
    AgenticLoop,
    LoopState,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.tool_executor import ToolExecutor
    from backend.llm.usage import LLMCallUsage
    from backend.tools.broker import ToolBroker


logger = logging.getLogger(__name__)

# --- Forced web search classifier prompt ---
FORCED_WEB_SEARCH_CLASSIFIER_PROMPT = (
    "Answer with exactly YES or NO.\n"
    "Return YES when the user's latest message explicitly asks to search the "
    "internet, search the web, look something up online, or when the answer "
    "depends on current external data that should be looked up before "
    "answering.\n"
    "Return NO otherwise.\n"
    "Treat the user's language as arbitrary: the decision must work for any "
    "language.\n"
    "Do not explain your answer."
)


class LLMCaller:
    """Handle LLM invocations with SSE streaming and forced search.

    Attributes:
        model_name: Name of the current LLM model.
        web_search_classifier_fn: Optional web search classifier callable.
        memory_recall_classifier_fn: Optional memory recall classifier callable.
        broker: Tool broker for forced tool execution.
    """

    def __init__(
        self,
        model_name: str,
        web_search_classifier_fn: Callable | None,
        memory_recall_classifier_fn: Callable | None,
        broker: ToolBroker,
    ) -> None:
        """Initialize LLM caller.

        Args:
            model_name: Name of the current LLM model.
            web_search_classifier_fn: Optional web search classifier.
            memory_recall_classifier_fn: Optional memory recall classifier.
            broker: Tool broker for forced tool execution.
        """
        self.model_name = model_name
        self.web_search_classifier_fn = web_search_classifier_fn
        self.memory_recall_classifier_fn = memory_recall_classifier_fn
        self.broker = broker
        self.sse_queue: object | None = None

    async def call_once(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        call_usages: list[LLMCallUsage],
        tool_choice: object | None = None,
    ) -> object:
        """Call the LLM once with the currently prepared prompt messages."""
        llm_response = await self.invoke(
            llm_call=llm_call,
            messages=messages,
            openai_tools=openai_tools,
            tool_choice=tool_choice,
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
        use_sse: bool = True,
        tool_choice: object | None = None,
    ) -> object:
        """Invoke the configured LLM with the supported optional arguments."""
        if use_sse and llm_accepts_sse_queue(llm_call):
            return await self.invoke_with_sse(
                llm_call=llm_call,
                messages=messages,
                openai_tools=openai_tools,
                tool_choice=tool_choice,
            )
        if llm_accepts_tools_override(llm_call):
            return await llm_call(
                messages,
                tools_override=openai_tools,
            )
        return await llm_call(messages)

    async def invoke_with_sse(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        tool_choice: object | None = None,
    ) -> object:
        """Invoke the configured LLM while forwarding tokens into the SSE queue."""
        if llm_accepts_tools_override(llm_call):
            kwargs: dict[str, object] = {
                "sse_queue_override": self.sse_queue,
                "tools_override": openai_tools,
            }
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            return await llm_call(messages, **kwargs)
        return await llm_call(
            messages,
            self.sse_queue,
        )

    async def should_force_web_search(
        self,
        *,
        filtered_message: str,
        llm_call: object,
        call_usages: list[LLMCallUsage],
    ) -> bool:
        """Decide whether one turn must fetch web results before the first answer."""
        if message_requires_forced_web_search(filtered_message):
            return True
        if not message_may_need_forced_web_search_classifier(filtered_message):
            return False
        if self.web_search_classifier_fn is not None:
            return await self.web_search_classifier_fn(filtered_message)
        classifier_response = await self.invoke(
            llm_call=llm_call,
            messages=[
                {"role": "system", "content": FORCED_WEB_SEARCH_CLASSIFIER_PROMPT},
                {"role": "user", "content": filtered_message},
            ],
            openai_tools=None,
            use_sse=False,
        )
        if classifier_response.usage is not None:
            call_usages.append(classifier_response.usage)
        return llm_requires_forced_web_search(classifier_response.text)

    async def should_force_memory_recall(
        self,
        filtered_message: str,
    ) -> bool:
        """Decide whether one turn must recall memory before the first answer."""
        if self.memory_recall_classifier_fn is None:
            return False
        if not message_may_need_memory_recall(filtered_message):
            return False
        try:
            return await self.memory_recall_classifier_fn(filtered_message)
        except Exception:
            logger.warning(
                "Memory recall classification failed",
                exc_info=True,
            )
            return False

    async def append_forced_web_search_context(
        self,
        *,
        filtered_message: str,
        messages: list[dict[str, object]],
        llm_call: object,
        call_usages: list[LLMCallUsage],
    ) -> None:
        """Run web_search early for explicit online-search requests."""
        try:
            should_force = await self.should_force_web_search(
                filtered_message=filtered_message,
                llm_call=llm_call,
                call_usages=call_usages,
            )
        except Exception:
            logger.warning(
                "Forced web_search classification failed",
                exc_info=True,
            )
            return
        if not should_force:
            return
        try:
            search_result = await self.broker.execute(
                "web_search",
                {"query": filtered_message},
            )
        except Exception:
            logger.warning(
                "Forced web_search preflight failed",
                exc_info=True,
            )
            return
        if not search_result.success:
            logger.warning(
                "Forced web_search preflight returned an error: %s",
                search_result.result,
            )
            return
        messages.append(
            {
                "role": "system",
                "content": (
                    "Web search results were fetched before the first answer "
                    "because the user explicitly asked to search online.\n"
                    f"{search_result.result}"
                ),
            }
        )

    async def append_forced_memory_recall_context(
        self,
        *,
        filtered_message: str,
        messages: list[dict[str, object]],
    ) -> int:
        """Run memory_recall early for questions about past conversations.

        Returns:
            Estimated token count of injected recall content (0 if nothing injected).
        """
        try:
            should_force = await self.should_force_memory_recall(
                filtered_message,
            )
        except Exception:
            logger.warning(
                "Forced memory_recall classification failed",
                exc_info=True,
            )
            return 0
        if not should_force:
            return 0
        try:
            recall_result = await self.broker.execute(
                "memory_recall",
                {"query": filtered_message},
            )
        except Exception:
            logger.warning(
                "Forced memory_recall preflight failed",
                exc_info=True,
            )
            return 0
        if not recall_result.success:
            return 0
        result_text = str(recall_result.result)
        if not result_text.strip():
            return 0
        recall_content = (
            f"Memory recall results from past conversations:\n{result_text}"
        )
        messages.append(
            {
                "role": "system",
                "content": recall_content,
            }
        )
        from backend.memory.context_builder.constants import estimate_tokens

        return estimate_tokens(recall_content)

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
        user_intent_tools: set[str] | None = None,
        force_web_search: bool = False,
    ) -> ModelLoopResult:
        """Run the model/tool loop until a final answer is available."""
        final_text = ""
        structured_content = None
        executed_immediate_tools = False
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

            llm_response = await self.call_once(
                llm_call=llm_call,
                messages=messages,
                openai_tools=openai_tools,
                call_usages=call_usages,
                tool_choice=forced_choice,
            )
            first_iteration = False
            round_result = await tool_executor.run_one_tool_round(
                llm_response=llm_response,
                messages=messages,
                channel=channel,
                user_intent_tools=user_intent_tools,
            )
            if round_result.final_text:
                final_text = round_result.final_text
                structured_content = round_result.structured_content
                break
            if round_result.executed_immediate_tools:
                executed_immediate_tools = True
            if not round_result.should_continue:
                break

            loop.advance()

        if not final_text.strip() and executed_immediate_tools:
            recovered_final_text = await request_non_empty_final_answer(
                llm_call=llm_call,
                messages=messages,
                call_usages=call_usages,
            )
            if recovered_final_text:
                final_text = recovered_final_text

        if final_text.strip():
            return ModelLoopResult(
                final_text=final_text,
                structured_content=structured_content,
            )
        return ModelLoopResult(
            final_text=(
                "I could not produce a final answer for that request. Please try again."
            ),
        )
