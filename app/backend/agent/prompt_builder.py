"""Prompt building delegate for the gateway orchestrator.

Builds system prompts, assembles LLM message arrays from context
entries, manages user profile injection, memory guardrails, and
evidence recall messages. Handles tool schema selection based on
user message content.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from backend.agent.agent_loop_helpers import (
    context_contains_active_long_term_preference,
    message_allows_file_write_tool,
)
from backend.memory.evidence_recall import (
    build_evidence_recall_context,
    is_evidence_recall_question,
)
from backend.processes.orchestrator.client import DBProxyError
from backend.security.policy import get_policy


_KEY_SYSTEM_PROMPT_ADDONS = "llm.system_prompt_addons"


def _policy_system_prompt_addons() -> str:
    """Return the assistant-policy system prompt addon text or empty."""
    return get_policy().get_str(_KEY_SYSTEM_PROMPT_ADDONS).strip()


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.agent_loop_models import PreparedContext
    from backend.memory.classification.question_router import MemoryQuestionRoute
    from backend.tools.broker import ToolBroker


logger = logging.getLogger(__name__)

# --- Constants ---
FILE_WRITE_TOOL_NAME = "file_write"
MEMORY_META_GUARDRAIL = (
    "Memory-answer rule: if retrieved memory already shows an active long-term "
    "preference, do not claim that no durable preference exists, that nothing "
    "persists, or that future chats start fresh without style memory. Keep "
    "temporary session/test context separate from the stored long-term "
    "preference. A temporary validation run, session identifier, or "
    "session-specific task context does not by itself cancel or erase an "
    "already retrieved durable answer-style preference. Do not conclude that "
    "a new style was session-only unless the retrieved memory explicitly says "
    "the preference was temporary."
)
RECOVERABLE_MEMORY_CALLBACK_ERRORS = (
    DBProxyError,
    RuntimeError,
    TypeError,
    ValueError,
)


class PromptBuilder:
    """Build system prompts and assemble LLM message arrays.

    Attributes:
        model_name: Name of the current LLM model.
        user_profile: User profile key-value pairs.
        broker: Tool broker for schema listing.
    """

    def __init__(
        self,
        model_name: str,
        user_profile: dict[str, str],
        broker: ToolBroker,
        evidence_bundle_fn: Callable | None,
    ) -> None:
        """Initialize prompt builder.

        Args:
            model_name: Name of the current LLM model.
            user_profile: User profile key-value pairs.
            broker: Tool broker for schema listing.
            evidence_bundle_fn: Callable returning evidence bundles.
        """
        self.model_name = model_name
        self.user_profile = user_profile
        self.broker = broker
        self.evidence_bundle_fn = evidence_bundle_fn
        self.skill_index: list[dict[str, str]] = []
        self.active_skill_context: str | None = None
        self.allowed_tool_names: set[str] | None = None
        self.scheduled_task_mode: bool = False

    def build_system_prompt(self) -> str:
        """Build system prompt for the orchestrator.

        Tool descriptions are passed via the API ``tools`` parameter,
        so the system prompt only contains persona, behavioral
        instructions, and user profile.
        """
        if self.scheduled_task_mode:
            return self._build_scheduled_task_system_prompt()

        prompt = (
            "<rules>\n"
            "- All timestamps must include seconds: 03:19:45, not 03:19\n"
            "- Do not use markdown tables\n"
            "- Do not use ### headers\n"
            "- Do not use emoji\n"
            "- Do not suggest saving files or tool calls unprompted\n"
            "- Write in a concise, conversational tone\n"
            "- Always use numbered or bulleted lists with one item per line; "
            "never write list items in a continuous paragraph\n"
            "- End every answer with exactly this format (three backticks, "
            "the word suggestions, newline, 3 lines, three backticks):\n"
            "```suggestions\n"
            "First action prompt\n"
            "Second action prompt\n"
            "Third action prompt\n"
            "```\n"
            "- Each suggestion must be written as if the user is typing it — "
            "a direct request from user to assistant, e.g. "
            "'Find the weekly exchange-rate forecast', "
            "'Create a reminder to check the exchange rate', "
            "'Search for ETH price history'\n"
            "- Never write suggestions addressed to the user; always address "
            "the assistant\n"
            "- Suggestions must only propose actions the assistant can perform "
            "using its available tools: web search, file operations, email, "
            "reminders, memory recall, URL fetch, and system status\n"
            "- Never suggest actions that require the user's browser or device: "
            "opening websites, installing apps, or downloading files\n"
            "- Each suggestion must be specific to the current topic, "
            "and written in the same language as the user's latest message\n"
            "- Do not mention the suggestions block in the main answer body\n"
            "- If <matched_skills> section is present, call skill_load(name) "
            "to load the full procedure before starting the task\n"
            "</rules>\n\n"
            "You are biMEMO, a helpful AI assistant"
            " with access to tools.\n\n"
            "Always use web_search when the user asks about"
            " current information, news, prices,"
            " or real-time data.\n\n"
            "Use memory_recall when the user asks about past"
            " conversations, previous decisions, their preferences,"
            " or anything discussed earlier. Do not guess — recall"
            " the actual memory first, then answer based on it."
        )

        # Inject current datetime
        prompt += "\n\n" + self.build_datetime_line()

        # Inject model identity
        if self.model_name:
            prompt += "\nLLM model: " + self.model_name

        # Inject user profile section if any field is non-empty
        profile_lines = self.build_profile_section()
        if profile_lines:
            prompt += "\n\n" + profile_lines

        # Inject skill hints if relevant skills were found by FTS
        if self.active_skill_context:
            prompt += (
                "\n\n<matched_skills>\n"
                "Relevant saved skills found. Call skill_load(name) "
                "to load the full procedure before starting:\n"
                + self.active_skill_context
                + "\n</matched_skills>"
            )

        # Append profile-driven addons last so they cannot be hidden
        # behind earlier content.
        addons = _policy_system_prompt_addons()
        if addons:
            prompt += "\n\n" + addons

        return prompt

    def _build_scheduled_task_system_prompt(self) -> str:
        """Build system prompt for scheduled task execution.

        Used when the agent runs a scheduled (cron) task without a live
        user. Removes interactive UX rules (no suggestions block, no
        addressing the user) and instructs the agent to report concisely
        or stay silent when there is nothing to report.
        """
        prompt = (
            "<rules>\n"
            "- You are executing a scheduled background task — "
            "there is no interactive user reading this in real time\n"
            "- Do NOT include a suggestions block in your response\n"
            "- Do NOT address the user directly with greetings or "
            "follow-up questions\n"
            "- Report concisely: state findings, results, or status only\n"
            "- All timestamps must include seconds: 03:19:45, not 03:19\n"
            "- Do not use markdown tables\n"
            "- Do not use ### headers\n"
            "- Do not use emoji\n"
            "- If there is nothing noteworthy to report (no changes, "
            "no new information, all checks passed), respond with "
            "exactly the marker '[SILENT]' on its own line and nothing "
            "else — the system will skip delivery\n"
            "- Use only the tools listed in your available tools — "
            "do not attempt to call tools outside the allowlist\n"
            "- If the task requires persisting findings to memory, "
            "use memory_save explicitly — dialog recorder is disabled "
            "for scheduled sessions\n"
            "</rules>\n\n"
            "You are biMEMO running an autonomous scheduled task.\n"
            "Execute the task instructions, gather any required "
            "information using your tools, and produce a concise report."
        )

        prompt += "\n\n" + self.build_datetime_line()

        if self.model_name:
            prompt += "\nLLM model: " + self.model_name

        return prompt

    def build_datetime_line(self) -> str:
        """Build the current datetime line for the system prompt.

        Uses the user's timezone from profile if available and valid,
        otherwise falls back to UTC.

        Returns:
            Formatted datetime string like
            ``Current datetime: 2026-03-10T14:30+03:00``.
        """
        tz_name = self.user_profile.get("timezone", "")
        tz = UTC
        if tz_name:
            try:
                tz = ZoneInfo(tz_name)
            except (KeyError, ValueError):
                logger.warning(
                    "Invalid timezone in user profile: %r, using UTC",
                    tz_name,
                )

        now = datetime.now(tz)
        return "Current datetime: " + now.strftime("%Y-%m-%d %H:%M:%S%z")

    def build_profile_section(self) -> str:
        """Build the User Profile section for the system prompt.

        Returns:
            Formatted profile section string, or empty string if
            no profile fields are set.
        """
        if not self.user_profile:
            return ""

        field_map = [
            ("display_name", "Name"),
            ("assistant_name", "Assistant name"),
            ("timezone", "Timezone"),
            ("language", "Language"),
            ("custom_instructions", "Instructions"),
        ]

        lines: list[str] = []
        for key, label in field_map:
            value = self.user_profile.get(key, "")
            if value:
                lines.append(f"- {label}: {value}")

        if not lines:
            return ""

        return "## User Profile\n" + "\n".join(lines)

    def build_prompt_messages(
        self,
        *,
        context_entries: list[object],
        memory_guidance: str | None,
        log_context_fn: object,
    ) -> list[dict[str, object]]:
        """Build the initial prompt message list for one user turn.

        Args:
            context_entries: Loaded context entries.
            memory_guidance: Optional memory guidance system message.
            log_context_fn: Callable to log context composition.
        """
        messages = self.build_initial_prompt_messages(
            memory_guidance=memory_guidance,
        )
        log_context_fn(context_entries)
        messages.extend(
            self.build_context_prompt_messages(context_entries),
        )
        return messages

    def build_initial_prompt_messages(
        self,
        *,
        memory_guidance: str | None,
    ) -> list[dict[str, object]]:
        """Build the initial system-level messages for one turn."""
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self.build_system_prompt()},
        ]
        if memory_guidance:
            messages.append({"role": "system", "content": memory_guidance})
        return messages

    def build_context_prompt_messages(
        self,
        context_entries: list[object],
    ) -> list[dict[str, object]]:
        """Convert loaded context entries into prompt messages."""
        messages: list[dict[str, object]] = []
        for context_entry in context_entries:
            role = (
                context_entry.role
                if context_entry.role in ("system", "user", "assistant", "tool")
                else "system"
            )
            messages.append({"role": role, "content": context_entry.content})
        return messages

    def append_memory_context_messages(
        self,
        *,
        messages: list[dict[str, object]],
        filtered_message: str,
        prepared_context: PreparedContext,
        route: MemoryQuestionRoute,
    ) -> None:
        """Append extra system context related to memory handling."""
        self.append_memory_guardrail_message(
            messages=messages,
            prepared_context=prepared_context,
            route=route,
        )

    async def append_memory_context_messages_async(
        self,
        *,
        messages: list[dict[str, object]],
        filtered_message: str,
        prepared_context: PreparedContext,
        route: MemoryQuestionRoute,
    ) -> None:
        """Append extra system context related to memory handling."""
        self.append_memory_guardrail_message(
            messages=messages,
            prepared_context=prepared_context,
            route=route,
        )
        evidence_recall_message = await self.build_evidence_recall_message(
            filtered_message=filtered_message,
            route=route,
        )
        if evidence_recall_message is not None:
            messages.append(evidence_recall_message)

    def append_memory_guardrail_message(
        self,
        *,
        messages: list[dict[str, object]],
        prepared_context: PreparedContext,
        route: MemoryQuestionRoute,
    ) -> None:
        """Append the memory guardrail when long-term preference context exists."""
        if route.memory_query and context_contains_active_long_term_preference(
            prepared_context.entries,
        ):
            messages.append(
                {
                    "role": "system",
                    "content": MEMORY_META_GUARDRAIL,
                }
            )

    async def build_evidence_recall_message(
        self,
        *,
        filtered_message: str,
        route: MemoryQuestionRoute,
    ) -> dict[str, str] | None:
        """Build the extra evidence-recall system message when it applies."""
        if (
            not route.memory_query
            or route.long_term_memory_question
            or self.evidence_bundle_fn is None
            or not is_evidence_recall_question(filtered_message)
        ):
            return None
        try:
            if inspect.iscoroutinefunction(self.evidence_bundle_fn):
                evidence_bundle = await self.evidence_bundle_fn(
                    filtered_message,
                )
            else:
                evidence_bundle = self.evidence_bundle_fn(filtered_message)
            if evidence_bundle is None or (
                not evidence_bundle.records and not evidence_bundle.source_messages
            ):
                return None
            return {
                "role": "system",
                "content": build_evidence_recall_context(
                    evidence_bundle,
                ),
            }
        except RECOVERABLE_MEMORY_CALLBACK_ERRORS:
            logger.warning(
                "Failed to build evidence recall context",
                exc_info=True,
            )
            return None

    def append_turn_reminder_and_user_message(
        self,
        *,
        messages: list[dict[str, object]],
        user_content: str | list[dict[str, object]],
    ) -> None:
        """Append the final turn reminder and current user message."""
        messages.append(
            {
                "role": "system",
                "content": (
                    "Reminder: no tables, no ### headers, no emoji."
                    " Timestamps always with seconds (HH:MM:SS)."
                ),
            }
        )
        messages.append({"role": "user", "content": user_content})

    def select_openai_tools(self, message: str) -> list[dict] | None:
        """Return the tool schemas that should be exposed for this message."""
        tool_names = getattr(self.broker.registry, "tool_names", None)
        if tool_names is None:
            return self.broker.list_openai_schemas()

        allowed_names = set(tool_names)
        if not message_allows_file_write_tool(message):
            allowed_names.discard(FILE_WRITE_TOOL_NAME)
        # Per-call allowlist restriction (used by scheduler for cron tasks)
        if self.allowed_tool_names is not None:
            allowed_names &= self.allowed_tool_names
        return self.broker.list_openai_schemas(allowed_names=allowed_names)
