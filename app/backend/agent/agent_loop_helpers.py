"""Stateless helper functions for the gateway agent loop.

Provides pure functions for message classification, tool-call
zone splitting, dialog record formatting, and LLM capability
detection. None of these functions hold state or perform I/O;
they only inspect and transform their inputs.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import inspect
import json
import re
from typing import TYPE_CHECKING

from backend.llm.structured_response import (
    MAX_STRUCTURED_SUGGESTIONS,
    StructuredAssistantContent,
    normalize_legacy_response_text,
)
from backend.trust.zones import TrustZone, get_zone


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.agent_loop_models import ToolCall
    from backend.llm.usage import LLMCallUsage
    from backend.tools.broker import ToolResult

# --- Dialog step formatting ---
TURN_OBSERVATION_USER_LABEL = "User"
TURN_OBSERVATION_ASSISTANT_LABEL = "Assistant"
TURN_OBSERVATION_SECTION_SEPARATOR = "\n\n"

# --- Long-term context detection ---
LONG_TERM_CONTEXT_MARKERS = (
    "long-term",
    "long term",
    "active preference",
    "future technical",
    "future debugging",
    "default technical-answer preference",
)
CURRENT_PREFERENCE_MARKERS = ("detailed", "step-by-step", "context", "reasoning")

# --- File write intent detection ---
FILE_WRITE_ACTION_MARKERS = (
    "write",
    "save",
    "create",
    "update",
    "edit",
    "modify",
    "overwrite",
    "append",
    "patch",
)
FILE_WRITE_TARGET_MARKERS = (
    "file",
    "path",
    "document",
    "record",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".py",
    ".ts",
    ".js",
    ".html",
    ".css",
    "readme",
    "config",
)

# --- File read/search intent detection ---
FILE_READ_ACTION_MARKERS = (
    "read",
    "open",
    "show",
    "inspect",
    "analyze",
    "analyse",
    "search",
    "find",
    "list",
    "cat",
    "grep",
    "прочитай",
    "открой",
    "покажи",
    "посмотри",
    "проанализируй",
    "анализируй",
    "найди",
    "поищи",
    "список",
)
FILE_READ_TARGET_MARKERS = (
    "file",
    "files",
    "path",
    "directory",
    "folder",
    "repo",
    "repository",
    "файл",
    "файлы",
    "путь",
    "папк",
    "директор",
    "репозитор",
)
PATH_LIKE_PATTERN = re.compile(
    r"(^|\s)(?:"
    r"(?:/[\w.@:+-][\w.@:+\-/]*[\w.@:+-])"
    r"|(?:~[/\w.@:+-][\w.@:+\-/]*[\w.@:+-])"
    r"|(?:[\w.@:+-]+/[\w.@:+\-/]*[\w.@:+-])"
    r")(?=\s|$|[.,;:!?])"
)

# --- Forced web search detection ---
FORCED_WEB_SEARCH_PHRASES = (
    "узнай в интернете",
    "узнай в сети",
    "поищи в интернете",
    "поищи в сети",
    "найди в интернете",
    "найди в сети",
    "проверь в интернете",
    "проверь в сети",
    "посмотри в интернете",
    "посмотри в сети",
    "search the web",
    "search online",
    "look it up online",
    "look it up on the web",
    "check online",
    "find online",
)
FORCED_WEB_SEARCH_HINT_MARKERS = (
    "internet",
    "internete",
    "internetu",
    "interneto",
    "internets",
    "interneten",
    "internetul",
    "online",
    "web",
    "www",
    "browser",
    "google",
    "bing",
    "searx",
    "search",
    "lookup",
    "look up",
    "поищи",
    "найди",
    "узнай",
    "проверь",
    "посмотри",
    "интернет",
    "сети",
)

# --- Memory negation ---
MEMORY_NEGATION_MARKERS = (
    "no durable preference",
    "no long-term preference",
    "nothing from this session should be remembered",
    "future chats start fresh",
    "will not persist",
    "won't persist",
)

# --- Empty final answer recovery ---
EMPTY_FINAL_ANSWER_RECOVERY_PROMPT = (
    "Provide the final user-facing answer now in plain text. "
    "Do not call tools again. Do not return an empty reply. "
    "Do not say that you are searching, checking, preparing, or looking things up; "
    "use the available context and give the substantive answer."
)
MISSING_SUGGESTIONS_RECOVERY_PROMPT = (
    "Generate follow-up suggestions for the already completed assistant answer. "
    "Return only one JSON object with this exact shape: "
    '{"suggestions":["first","second","third"]}. '
    "Do not use markdown. Do not call tools. Do not rewrite the answer. "
    "Each suggestion must be written as a direct request from the user to the "
    "assistant, in the same language as the user's message. Suggestions must be "
    "specific to the topic and must only ask for actions the assistant can do."
)
MESSAGE_META_STATS_KEY = "stats"
MESSAGE_META_STRUCTURED_CONTENT_KEY = "structured_content"


def build_assistant_message_meta(
    stats: object | None,
    structured_content: StructuredAssistantContent | None,
) -> dict[str, object] | None:
    """Build persisted metadata for one saved assistant message.

    The visible reply text is stored in the main message content field, so the
    metadata keeps only extra sections and usage data.
    """
    message_meta: dict[str, object] = {}
    if stats is not None:
        message_meta[MESSAGE_META_STATS_KEY] = stats.model_dump(mode="json")
    if structured_content is not None:
        message_meta[MESSAGE_META_STRUCTURED_CONTENT_KEY] = {
            "thinking": structured_content.thinking,
            "sql": structured_content.sql,
            "suggestions": structured_content.suggestions,
        }
    if not message_meta:
        return None
    return message_meta


async def request_non_empty_final_answer(
    *,
    llm_call: Callable,
    messages: list[dict],
    call_usages: list[LLMCallUsage],
) -> StructuredAssistantContent | None:
    """Ask the model once more for one final assistant answer after tool use."""
    recovery_messages = [
        *messages,
        {"role": "system", "content": EMPTY_FINAL_ANSWER_RECOVERY_PROMPT},
    ]
    if llm_accepts_sse_queue(llm_call):
        recovery_response = await llm_call(
            recovery_messages,
            sse_queue_override=None,
            tools_override=[],
        )
    elif llm_accepts_tools_override(llm_call):
        recovery_response = await llm_call(
            recovery_messages,
            tools_override=[],
        )
    else:
        recovery_response = await llm_call(recovery_messages)
    if recovery_response.usage is not None:
        call_usages.append(recovery_response.usage)
    if recovery_response.structured_content is not None:
        return recovery_response.structured_content
    if not recovery_response.text.strip():
        return None
    return normalize_legacy_response_text(recovery_response.text)


def extract_recovered_suggestions(response: object) -> list[str]:
    """Extract suggestions from a lightweight recovery response."""
    structured_content = getattr(response, "structured_content", None)
    if structured_content is not None and structured_content.suggestions:
        return structured_content.suggestions[:MAX_STRUCTURED_SUGGESTIONS]

    response_text = str(getattr(response, "text", "") or "").strip()
    if not response_text:
        return []
    try:
        raw_data = json.loads(response_text)
    except json.JSONDecodeError:
        return normalize_legacy_response_text(response_text).suggestions[
            :MAX_STRUCTURED_SUGGESTIONS
        ]
    if not isinstance(raw_data, dict):
        return []
    raw_suggestions = raw_data.get("suggestions", [])
    if not isinstance(raw_suggestions, list):
        return []
    suggestions = [
        str(suggestion).strip()
        for suggestion in raw_suggestions
        if str(suggestion).strip()
    ]
    return suggestions[:MAX_STRUCTURED_SUGGESTIONS]


def latest_user_message_text(messages: list[dict]) -> str:
    """Return the latest user message text from one prompt message list."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


async def request_missing_suggestions(
    *,
    llm_call: Callable,
    messages: list[dict],
    answer_text: str,
    call_usages: list[LLMCallUsage],
) -> list[str]:
    """Ask the model once for suggestions when the final answer omitted them."""
    recovery_messages = [
        {
            "role": "system",
            "content": MISSING_SUGGESTIONS_RECOVERY_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"Latest user message:\n{latest_user_message_text(messages)}\n\n"
                f"Completed assistant answer:\n{answer_text}\n\n"
                "Generate exactly three suggestions."
            ),
        },
    ]
    if llm_accepts_sse_queue(llm_call):
        recovery_response = await llm_call(
            recovery_messages,
            sse_queue_override=None,
            tools_override=[],
        )
    elif llm_accepts_tools_override(llm_call):
        recovery_response = await llm_call(
            recovery_messages,
            tools_override=[],
        )
    else:
        recovery_response = await llm_call(recovery_messages)
    if recovery_response.usage is not None:
        call_usages.append(recovery_response.usage)
    return extract_recovered_suggestions(recovery_response)


def build_dialog_step_record_input(
    user_text: str,
    response_text: str,
) -> str:
    """Build a compact dialog-step transcript for dialog recorder compression."""
    return TURN_OBSERVATION_SECTION_SEPARATOR.join(
        [
            f"{TURN_OBSERVATION_USER_LABEL}: {user_text}",
            f"{TURN_OBSERVATION_ASSISTANT_LABEL}: {response_text}",
        ]
    )


def context_contains_active_long_term_preference(
    context_entries: list[object],
) -> bool:
    """Return True when prompt context already contains a long-term preference."""
    for entry in context_entries:
        role = str(getattr(entry, "role", ""))
        content = str(getattr(entry, "content", "")).lower()
        if role != "summary":
            continue
        if not any(marker in content for marker in LONG_TERM_CONTEXT_MARKERS):
            continue
        if any(marker in content for marker in MEMORY_NEGATION_MARKERS):
            continue
        if any(marker in content for marker in CURRENT_PREFERENCE_MARKERS):
            return True
    return False


def split_by_zone(
    calls: list[ToolCall],
    user_intent_tools: set[str] | None = None,
) -> tuple[list[ToolCall], list[ToolCall]]:
    """Split tool calls by trust zone.

    GREEN and YELLOW tools execute immediately.
    RED tools require individual confirmation unless
    the user explicitly requested the action.

    Args:
        calls: Tool calls to classify.
        user_intent_tools: Tool names the user explicitly requested.

    Returns:
        Tuple of (immediate calls, red calls).
    """
    immediate: list[ToolCall] = []
    red: list[ToolCall] = []
    intent_set = user_intent_tools or set()
    for call in calls:
        zone = get_zone(call.name)
        if zone == TrustZone.RED and call.name not in intent_set:
            red.append(call)
        else:
            immediate.append(call)
    return immediate, red


def build_tool_result_messages(
    calls: list[ToolCall],
    results: list[ToolResult],
) -> list[dict]:
    """Build OpenAI-format tool result messages.

    Args:
        calls: Tool calls that were executed.
        results: Corresponding execution results.

    Returns:
        List of ``role: tool`` message dicts.
    """
    return [
        {
            "role": "tool",
            "tool_call_id": call.id,
            "content": str(result.result),
        }
        for call, result in zip(calls, results, strict=True)
    ]


def llm_accepts_sse_queue(llm_call: object) -> bool:
    """Check whether the LLM callable accepts an SSE queue argument."""
    try:
        return len(inspect.signature(llm_call).parameters) >= 2
    except TypeError, ValueError:
        return True


def llm_accepts_tools_override(llm_call: object) -> bool:
    """Check whether the LLM callable accepts a tools override keyword."""
    try:
        parameters = inspect.signature(llm_call).parameters
    except TypeError, ValueError:
        return False

    if "tools_override" in parameters:
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def message_allows_file_write_tool(message: str) -> bool:
    """Return True when the user clearly asked to create or edit a file."""
    normalized_message = message.casefold()
    has_action_marker = any(
        marker in normalized_message for marker in FILE_WRITE_ACTION_MARKERS
    )
    has_target_marker = any(
        marker in normalized_message for marker in FILE_WRITE_TARGET_MARKERS
    )
    return has_action_marker and has_target_marker


def message_allows_file_read_tools(message: str) -> bool:
    """Return True when the user clearly asked to read or search files."""
    normalized_message = message.casefold()
    has_action_marker = any(
        marker in normalized_message for marker in FILE_READ_ACTION_MARKERS
    )
    has_target_marker = any(
        marker in normalized_message for marker in FILE_READ_TARGET_MARKERS
    )
    has_path_like_target = PATH_LIKE_PATTERN.search(message) is not None
    return has_action_marker and (has_target_marker or has_path_like_target)


def message_requires_forced_web_search(message: str) -> bool:
    """Return True when the user explicitly asks to search online."""
    normalized_message = " ".join(message.casefold().split())
    return any(phrase in normalized_message for phrase in FORCED_WEB_SEARCH_PHRASES)


def message_may_need_forced_web_search_classifier(message: str) -> bool:
    """Return True when one message looks like an online-search request."""
    normalized_message = " ".join(message.casefold().split())
    return any(
        marker in normalized_message for marker in FORCED_WEB_SEARCH_HINT_MARKERS
    )


MEMORY_RECALL_HINT_MARKERS = (
    "помнишь",
    "вспомни",
    "remember",
    "recall",
    "previously",
    "прошлый раз",
    "прошлом разговоре",
    "раньше говорили",
    "обсуждали раньше",
    "last time",
    "earlier",
    "past conversation",
    "we discussed",
    "мы обсуждали",
    "о чём говорили",
)


def message_may_need_memory_recall(message: str) -> bool:
    """Return True when one message looks like a past-conversation question."""
    normalized_message = " ".join(message.casefold().split())
    return any(marker in normalized_message for marker in MEMORY_RECALL_HINT_MARKERS)


def llm_requires_forced_web_search(response_text: str) -> bool:
    """Interpret one YES/NO classifier answer for forced web search."""
    normalized_response = response_text.strip().casefold()
    return normalized_response.startswith("yes")
