"""Typed native decisions for semantic Stage 1 planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


ASK_USER_TOOL_NAME = "bimemo_ask_user"
CONTROL_TOOL_NAMES = frozenset({ASK_USER_TOOL_NAME})

SEMANTIC_CONTROL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": ASK_USER_TOOL_NAME,
            "description": (
                "Pause for one concrete missing value that cannot be obtained with "
                "an available tool. Never use this to ask tool permission."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4096,
                    },
                    "input_type": {
                        "type": "string",
                        "enum": ["string", "number", "integer", "boolean"],
                    },
                    "description": {"type": "string", "maxLength": 1024},
                },
                "required": ["question", "input_type"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
)


class SemanticDecisionKind(StrEnum):
    """Closed decision set for one Stage 1 provider response."""

    EXTERNAL_TOOLS = "external_tools"
    FINISH = "finish"
    ASK_USER = "ask_user"


class SemanticProtocolError(ValueError):
    """The provider did not return one valid native semantic decision."""


@dataclass(frozen=True)
class SemanticDecision:
    """Validated control or external-tool decision."""

    kind: SemanticDecisionKind
    external_calls: tuple[object, ...] = ()
    answer: str = ""
    question: str = ""
    expected_input_schema: dict[str, Any] | None = None


def with_semantic_controls(external_tools: list[dict] | None) -> list[dict]:
    """Return eligible external schemas plus backend-owned controls."""
    tools = [dict(item) for item in external_tools or []]
    external_names = {
        str(item.get("function", {}).get("name", ""))
        for item in tools
        if isinstance(item.get("function"), dict)
    }
    collision = external_names & CONTROL_TOOL_NAMES
    if collision:
        raise SemanticProtocolError(
            f"registered tools collide with backend controls: {sorted(collision)}"
        )
    tools.extend(SEMANTIC_CONTROL_SCHEMAS)
    return tools


def parse_semantic_decision(
    response: object,
    *,
    allowed_external_names: set[str] | None = None,
) -> SemanticDecision:
    """Validate one model decision: answer, question, or external calls."""
    calls = tuple(getattr(response, "tool_calls", ()) or ())
    if not calls:
        answer = getattr(response, "text", None)
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 65536:
            raise SemanticProtocolError(
                "Stage 1 returned no usable answer or tool call"
            )
        return SemanticDecision(
            kind=SemanticDecisionKind.FINISH,
            answer=answer.strip(),
        )
    controls = tuple(
        call for call in calls if getattr(call, "name", "") in CONTROL_TOOL_NAMES
    )
    if controls and (len(controls) != 1 or len(calls) != 1):
        raise SemanticProtocolError("control calls cannot be mixed or batched")
    if not controls:
        if allowed_external_names is not None:
            unknown = sorted(
                {
                    str(getattr(call, "name", ""))
                    for call in calls
                    if getattr(call, "name", "") not in allowed_external_names
                }
            )
            if unknown:
                raise SemanticProtocolError(
                    f"unregistered or ineligible external tools: {unknown}"
                )
        return SemanticDecision(
            kind=SemanticDecisionKind.EXTERNAL_TOOLS,
            external_calls=calls,
        )

    call = controls[0]
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, dict):
        raise SemanticProtocolError("control arguments must be an object")
    allowed = {"question", "input_type", "description"}
    if not set(arguments) <= allowed:
        raise SemanticProtocolError("bimemo_ask_user has invalid arguments")
    question = arguments.get("question")
    input_type = arguments.get("input_type")
    description = arguments.get("description", "")
    if not isinstance(question, str) or not question.strip() or len(question) > 4096:
        raise SemanticProtocolError("bimemo_ask_user requires a valid question")
    if input_type not in {"string", "number", "integer", "boolean"}:
        raise SemanticProtocolError("bimemo_ask_user has an unsupported input type")
    if not isinstance(description, str) or len(description) > 1024:
        raise SemanticProtocolError("bimemo_ask_user has an invalid description")
    schema: dict[str, Any] = {"type": input_type}
    if description:
        schema["description"] = description
    return SemanticDecision(
        kind=SemanticDecisionKind.ASK_USER,
        question=question.strip(),
        expected_input_schema=schema,
    )


__all__ = [
    "ASK_USER_TOOL_NAME",
    "CONTROL_TOOL_NAMES",
    "SEMANTIC_CONTROL_SCHEMAS",
    "SemanticDecision",
    "SemanticDecisionKind",
    "SemanticProtocolError",
    "parse_semantic_decision",
    "with_semantic_controls",
]
