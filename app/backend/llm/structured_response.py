"""Helpers for structured assistant responses.

Contract summary for assistant replies:

- ``StructuredAssistantContent.answer`` is the final user-facing reply text.
- Stored assistant messages keep that same final reply text in
  ``messages.content``.
- ``messages.meta["structured_content"]`` keeps only extra sections that are
  not duplicated in ``messages.content``: ``thinking`` and ``suggestions``.
- ``raw_final_text`` means the raw model output before output filtering.
- ``response_text`` means the final reply text after output filtering.

This keeps one clear split:
- main reply text lives in ``content`` and ``StructuredAssistantContent.answer``
- extra sections live in structured metadata
- raw model output stays an internal lower-layer value only
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

# --- Structured response parsing ---
MAX_STRUCTURED_SUGGESTIONS = 3
SCRIPT_DOMINANCE_THRESHOLD = 0.8
LATIN_SCRIPT_PATTERN = re.compile(r"[A-Za-z]")
CYRILLIC_SCRIPT_PATTERN = re.compile(r"[\u0400-\u04FF]")
SUGGESTIONS_BLOCK_PATTERN = re.compile(
    r"```suggestions\s*\n([\s\S]*?)```",
    re.IGNORECASE,
)

# --- Structured response contract ---
STRUCTURED_RESPONSE_SCHEMA_NAME = "assistant_response"
STRUCTURED_RESPONSE_INSTRUCTION = (
    "When you return a final answer, return only one JSON object that matches "
    'the requested schema. Put the visible user-facing answer into "answer". '
    'Put up to three follow-up suggestions into "suggestions" as plain strings. '
    "Do not wrap the JSON in markdown. When you need a tool, call the tool and "
    "leave the final answer empty until the tool results are available. "
    'If the schema includes "tool_plan", self-check before finalizing: set '
    "needs_tool and name the single tool you intend to use (or null). If you are "
    "unsure whether a tool or action is needed to answer correctly, set "
    'needs_clarification true and put one short clarifying question in "question" '
    "instead of guessing an answer."
)
STRUCTURED_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "tool_plan": {
            "type": "object",
            "properties": {
                "needs_tool": {"type": "boolean"},
                "tool": {"type": ["string", "null"]},
                "needs_clarification": {"type": "boolean"},
                "question": {"type": ["string", "null"]},
            },
            "required": ["needs_tool", "tool"],
            "additionalProperties": False,
        },
    },
    "required": ["answer", "suggestions"],
    "additionalProperties": False,
}
OPENAI_STRUCTURED_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": STRUCTURED_RESPONSE_SCHEMA_NAME,
        "strict": True,
        "schema": STRUCTURED_RESPONSE_SCHEMA,
    },
}

# --- Skill-extraction response contract ---
SKILL_EXTRACTION_SCHEMA_NAME = "skill_extraction"
SKILL_EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "trigger": {"type": "string"},
        "procedure": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "query": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["tool", "query", "note"],
                "additionalProperties": False,
            },
        },
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["name", "fix"],
                "additionalProperties": False,
            },
        },
        "verification": {"type": "string"},
    },
    "required": [
        "title",
        "description",
        "trigger",
        "procedure",
        "errors",
        "verification",
    ],
    "additionalProperties": False,
}
OPENAI_SKILL_EXTRACTION_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": SKILL_EXTRACTION_SCHEMA_NAME,
        "strict": True,
        "schema": SKILL_EXTRACTION_SCHEMA,
    },
}


# --- Skill-quality appraisal response contract ---
SKILL_QUALITY_SCHEMA_NAME = "skill_quality_appraisal"
SKILL_QUALITY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "reusability": {"type": "integer", "minimum": 0, "maximum": 25},
        "step_clarity": {"type": "integer", "minimum": 0, "maximum": 25},
        "future_applicability": {"type": "integer", "minimum": 0, "maximum": 25},
        "procedure_soundness": {"type": "integer", "minimum": 0, "maximum": 25},
        "appraisal_note": {"type": "string"},
    },
    "required": [
        "reusability",
        "step_clarity",
        "future_applicability",
        "procedure_soundness",
        "appraisal_note",
    ],
    "additionalProperties": False,
}
OPENAI_SKILL_QUALITY_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": SKILL_QUALITY_SCHEMA_NAME,
        "strict": True,
        "schema": SKILL_QUALITY_SCHEMA,
    },
}


@dataclass(frozen=True)
class StructuredAssistantContent:
    """Structured assistant content ready for UI rendering.

    ``answer`` is always the final reply text shown to the user.
    The optional fields store extra sections that are rendered separately and
    are stored in message metadata instead of duplicating the main reply text.
    """

    answer: str
    thinking: str | None = None
    suggestions: list[str] = field(default_factory=list)
    # Model-declared tool intent: {"needs_tool": bool, "tool": str|None}.
    tool_plan: dict | None = None


def _detect_script_profile(text: str) -> str:
    """Return the dominant visible script for one text fragment."""
    latin_count = len(LATIN_SCRIPT_PATTERN.findall(text))
    cyrillic_count = len(CYRILLIC_SCRIPT_PATTERN.findall(text))
    if latin_count == 0 and cyrillic_count == 0:
        return "neutral"
    if latin_count > 0 and cyrillic_count == 0:
        return "latin"
    if cyrillic_count > 0 and latin_count == 0:
        return "cyrillic"
    dominant_count = max(latin_count, cyrillic_count)
    total_count = latin_count + cyrillic_count
    if dominant_count / total_count >= SCRIPT_DOMINANCE_THRESHOLD:
        return "latin" if latin_count > cyrillic_count else "cyrillic"
    return "mixed"


def _suggestions_match_content_language(
    answer: str,
    suggestions: list[str],
) -> bool:
    """Check whether extracted suggestions match the answer language."""
    answer_profile = _detect_script_profile(answer)
    if answer_profile in {"neutral", "mixed"}:
        return True
    suggestions_profile = _detect_script_profile(" ".join(suggestions))
    if suggestions_profile in {"neutral", "mixed"}:
        return True
    return answer_profile == suggestions_profile


def _trim_suggestions(suggestions: list[str]) -> list[str]:
    """Trim suggestions to the supported count and language."""
    return suggestions[:MAX_STRUCTURED_SUGGESTIONS]


def _split_suggestions_block(text: str) -> tuple[str, list[str]]:
    """Split one suggestions fenced block out of the final assistant text."""
    suggestions_found: list[str] = []

    def _collect_block(match: re.Match[str]) -> str:
        block_lines = [
            line.strip() for line in match.group(1).splitlines() if line.strip()
        ]
        suggestions_found.extend(block_lines)
        return ""

    answer = SUGGESTIONS_BLOCK_PATTERN.sub(_collect_block, text).strip()
    if not suggestions_found:
        return answer, []
    trimmed_suggestions = _trim_suggestions(suggestions_found)
    if not _suggestions_match_content_language(answer, trimmed_suggestions):
        return answer, []
    return answer, trimmed_suggestions


def normalize_legacy_response_text(text: str) -> StructuredAssistantContent:
    """Normalize one markdown-style assistant answer into typed sections."""
    answer, suggestions = _split_suggestions_block(text)
    return StructuredAssistantContent(
        answer=answer,
        suggestions=suggestions,
    )


# Some structured-output backends (seen with vLLM guided decoding on certain
# quantized models) glue a spurious opening brace in front of the JSON object,
# e.g. '{{"answer": ...}' or '{"{ "answer": ...}'. That is invalid JSON. This
# bound limits how far into the reply we look for the real object start when
# repairing such a leading-brace defect.
LEADING_BRACE_REPAIR_SCAN = 16


def _loads_lenient(text: str) -> object:
    """Parse JSON, repairing a spurious leading brace when strict parsing fails.

    A well-formed body is parsed by ``json.loads`` unchanged. Only when that
    raises do we retry: scan the first few characters for each ``{`` and decode
    from there, returning the first object that parses. This rescues replies
    from misbehaving structured-output backends that prepend an extra ``{`` (or
    ``{"``) before the real object, without touching valid input.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as strict_error:
        decoder = json.JSONDecoder()
        for index in range(min(len(text), LEADING_BRACE_REPAIR_SCAN)):
            if text[index] != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                logger.warning(
                    "Repaired malformed structured JSON with a spurious leading "
                    "brace (parsed from offset %d)",
                    index,
                )
                return candidate
        raise strict_error


def parse_structured_response_json(
    text: str,
    *,
    thinking: str | None = None,
) -> StructuredAssistantContent:
    """Parse one JSON response body into typed structured assistant content.

    Empty or whitespace-only input yields an empty
    ``StructuredAssistantContent`` rather than raising, so providers that
    return a blank body cannot crash the client. The client-level retry
    logic is the primary defence; this guard is the safety net.
    """
    if not text or not text.strip():
        return StructuredAssistantContent(
            answer="",
            thinking=thinking.strip() if thinking else None,
        )
    raw_data = _loads_lenient(text)
    if not isinstance(raw_data, dict):
        raise ValueError("Structured response must be a JSON object")
    raw_answer = str(raw_data.get("answer", ""))
    answer, extracted_suggestions = _split_suggestions_block(raw_answer)
    raw_suggestions = raw_data.get("suggestions", [])
    if not isinstance(raw_suggestions, list):
        raise ValueError("Structured response suggestions must be a list")
    suggestions = [
        str(suggestion).strip()
        for suggestion in raw_suggestions
        if str(suggestion).strip()
    ]
    combined_suggestions = suggestions + [
        suggestion
        for suggestion in extracted_suggestions
        if suggestion not in suggestions
    ]
    trimmed_suggestions = _trim_suggestions(combined_suggestions)
    if not _suggestions_match_content_language(answer, trimmed_suggestions):
        trimmed_suggestions = []
    raw_tool_plan = raw_data.get("tool_plan")
    tool_plan = raw_tool_plan if isinstance(raw_tool_plan, dict) else None
    return StructuredAssistantContent(
        answer=answer.strip(),
        thinking=thinking.strip() if thinking else None,
        suggestions=trimmed_suggestions,
        tool_plan=tool_plan,
    )
