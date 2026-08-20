"""OpenAI-compatible response message parsing.

Pure helpers that turn one non-streaming OpenAI-style message dict into
tool calls, reasoning text, structured content, and the raw assistant
message stored in conversation history. Used by the client's
structured chat path.
"""

from __future__ import annotations

import json

from backend.llm.call_factory import LLMToolCall
from backend.llm.openai_compatible.protocol import (
    EMPTY_ASSISTANT_CONTENT,
    OpenAICompatibleClientError,
)
from backend.llm.structured_response import (
    StructuredAssistantContent,
    parse_final_only_response_json,
    parse_structured_response_json,
)


def _structured_content_from_message(
    message: dict[str, object],
    *,
    use_final_only_parser: bool = False,
) -> StructuredAssistantContent | None:
    """Build one structured assistant response from a non-streaming reply."""
    tool_calls = _tool_calls_from_message(message)
    if tool_calls:
        return None
    thinking = _thinking_from_message(message)
    parser = (
        parse_final_only_response_json
        if use_final_only_parser
        else parse_structured_response_json
    )
    return parser(
        str(message.get("content", EMPTY_ASSISTANT_CONTENT)),
        thinking=thinking,
    )


def _thinking_from_message(
    message: dict[str, object],
) -> str | None:
    """Return the separate reasoning text when the provider sent it."""
    for key in ("reasoning_content", "reasoning", "thinking"):
        raw_value = message.get(key)
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value
    return None


def _tool_calls_from_message(
    message: dict[str, object],
) -> list[LLMToolCall]:
    """Convert one non-streaming OpenAI-style tool call list."""
    raw_tool_calls = message.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []
    tool_calls: list[LLMToolCall] = []
    for tool_call_index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            continue
        function = raw_tool_call.get("function", {})
        if not isinstance(function, dict):
            continue
        raw_arguments = function.get("arguments", "")
        arguments = _parsed_tool_arguments(raw_arguments)
        tool_calls.append(
            LLMToolCall(
                id=str(
                    raw_tool_call.get(
                        "id",
                        f"openai-compatible-call-{tool_call_index}",
                    )
                ),
                name=str(function.get("name", "")),
                arguments=arguments,
            )
        )
    return tool_calls


def _parsed_tool_arguments(
    raw_arguments: object,
) -> dict:
    """Parse one OpenAI-style tool-call argument payload into a dict."""
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if raw_arguments is None:
        return {}
    if not isinstance(raw_arguments, str):
        raise OpenAICompatibleClientError(
            "Provider returned missing tool call arguments"
        )
    # An empty/whitespace string is how providers encode a call to a
    # zero-parameter tool; treat it as an empty argument object.
    if not raw_arguments.strip():
        return {}
    try:
        parsed_arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise OpenAICompatibleClientError(
            "Provider returned malformed tool call arguments"
        ) from exc
    if isinstance(parsed_arguments, dict):
        return parsed_arguments
    raise OpenAICompatibleClientError(
        "Provider returned non-object tool call arguments"
    )


def _raw_message_from_response(
    *,
    text: str,
    tool_calls: list[LLMToolCall],
) -> dict[str, object]:
    """Build one assistant message payload for downstream storage."""
    raw_message: dict[str, object] = {
        "role": "assistant",
        "content": text or None,
    }
    if tool_calls:
        raw_message["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.arguments),
                },
            }
            for tool_call in tool_calls
        ]
    return raw_message
