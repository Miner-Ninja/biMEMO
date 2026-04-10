"""Semantic fallback classifier for low-confidence memory questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.llm.ollama.models import ChatMessage
from backend.memory.classification.models import (
    ClassificationConfidence,
    MemoryQuestionClassification,
    MemoryQuestionIntent,
    build_memory_question_classification,
)


if TYPE_CHECKING:
    from backend.llm.ollama.provider import OllamaProvider


FALLBACK_CLASSIFIER_SYSTEM_PROMPT = (
    "You classify one user question for memory-routing.\n"
    "Return ONLY this exact plain-text format:\n"
    "INTENT: <non_memory_question|memory_recall_question|"
    "assistant_recall_question|user_recall_question|"
    "long_term_memory_question>\n"
    "ROLE_FILTER: <assistant|user|none>\n"
    "CONFIDENCE: <low|medium|high>\n"
    "LONG_TERM_MEMORY: <yes|no>\n"
    "RATIONALE: <short explanation>\n"
    "Rules:\n"
    "- Focus only on routing intent, not on answering the question\n"
    "- Use assistant_recall_question when the user asks what the assistant said"
    "\n"
    "- Use user_recall_question when the user asks what the user said\n"
    "- Use long_term_memory_question when the question asks what should remain "
    "durable in future conversations\n"
    "- If the question is about remembered context but not specifically "
    "assistant/user recall or long-term durability, use memory_recall_question\n"
    "- If it is not a memory-routing question, use non_memory_question\n"
    "/no_think"
)
FALLBACK_ROLE_NONE = "none"
FALLBACK_LONG_TERM_YES = "yes"
FALLBACK_INTENT_PREFIX = "INTENT:"
FALLBACK_ROLE_FILTER_PREFIX = "ROLE_FILTER:"
FALLBACK_CONFIDENCE_PREFIX = "CONFIDENCE:"
FALLBACK_LONG_TERM_PREFIX = "LONG_TERM_MEMORY:"
FALLBACK_RATIONALE_PREFIX = "RATIONALE:"
FALLBACK_MAX_TOKENS = 120
FALLBACK_TEMPERATURE = 0.0


@dataclass(frozen=True)
class SemanticFallbackResult:
    """Parsed result from the semantic fallback classifier.

    Attributes:
        classification: Structured classification result.
        rationale: Short explanation returned by the fallback model.
        raw_output: Raw LLM output before parsing.
    """

    classification: MemoryQuestionClassification
    rationale: str
    raw_output: str


@dataclass(frozen=True)
class ParsedFallbackFields:
    """Raw labeled fields extracted from one fallback classifier answer."""

    intent: str
    role_filter: str
    confidence: str
    long_term_memory: str
    rationale: str


def _line_value(raw_output: str, prefix: str) -> str:
    """Extract one field value from the fallback plain-text response."""
    for line in raw_output.splitlines():
        stripped_line = line.strip()
        if stripped_line.startswith(prefix):
            return stripped_line.removeprefix(prefix).strip()
    return ""


def _parse_intent(raw_value: str) -> MemoryQuestionIntent:
    """Parse the fallback intent value into the canonical intent enum."""
    normalized = raw_value.strip().lower()
    try:
        return MemoryQuestionIntent(normalized)
    except ValueError:
        return MemoryQuestionIntent.NON_MEMORY_QUESTION


def _parse_confidence(raw_value: str) -> ClassificationConfidence:
    """Parse the fallback confidence value into the confidence enum."""
    normalized = raw_value.strip().lower()
    try:
        return ClassificationConfidence(normalized)
    except ValueError:
        return ClassificationConfidence.LOW


def _normalize_fallback_route_fields(
    *,
    intent: MemoryQuestionIntent,
    role_filter: str | None,
    long_term_memory_question: bool,
) -> tuple[str | None, bool]:
    """Normalize contradictory fallback fields into one consistent route."""
    if intent is MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION:
        return None, True
    if intent in {
        MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
        MemoryQuestionIntent.USER_RECALL_QUESTION,
    }:
        return role_filter, False
    return role_filter, long_term_memory_question


def _build_fallback_classification(
    *,
    intent: MemoryQuestionIntent,
    role_filter: str | None,
    confidence: ClassificationConfidence,
    long_term_memory_question: bool,
) -> MemoryQuestionClassification:
    """Build one normalized fallback classification result."""
    return build_memory_question_classification(
        intent=intent,
        role_filter=role_filter,
        memory_query=intent is not MemoryQuestionIntent.NON_MEMORY_QUESTION,
        long_term_memory_question=long_term_memory_question,
        confidence=confidence,
    )


def _parsed_fallback_fields(raw_output: str) -> ParsedFallbackFields:
    """Extract the raw labeled fields from one fallback classifier output."""
    return ParsedFallbackFields(
        intent=_line_value(raw_output, FALLBACK_INTENT_PREFIX),
        role_filter=_line_value(raw_output, FALLBACK_ROLE_FILTER_PREFIX).lower(),
        confidence=_line_value(raw_output, FALLBACK_CONFIDENCE_PREFIX),
        long_term_memory=_line_value(raw_output, FALLBACK_LONG_TERM_PREFIX),
        rationale=_line_value(raw_output, FALLBACK_RATIONALE_PREFIX),
    )


def parse_fallback_classification(raw_output: str) -> SemanticFallbackResult:
    """Parse the fallback classifier output into a typed result.

    Args:
        raw_output: Plain-text fallback LLM response.

    Returns:
        Parsed semantic fallback result.
    """
    fields = _parsed_fallback_fields(raw_output)
    intent = _parse_intent(fields.intent)
    confidence = _parse_confidence(fields.confidence)

    role_filter = (
        None if fields.role_filter in {"", FALLBACK_ROLE_NONE} else fields.role_filter
    )
    long_term_memory_question = (
        fields.long_term_memory.strip().lower() == FALLBACK_LONG_TERM_YES
        or intent is MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION
    )
    role_filter, long_term_memory_question = _normalize_fallback_route_fields(
        intent=intent,
        role_filter=role_filter,
        long_term_memory_question=long_term_memory_question,
    )
    classification = _build_fallback_classification(
        intent=intent,
        role_filter=role_filter,
        confidence=confidence,
        long_term_memory_question=long_term_memory_question,
    )
    return SemanticFallbackResult(
        classification=classification,
        rationale=fields.rationale,
        raw_output=raw_output,
    )


async def classify_memory_question_with_fallback(
    provider: OllamaProvider,
    text: str,
) -> SemanticFallbackResult:
    """Classify one low-confidence memory question through the LLM fallback.

    Args:
        provider: LLM provider used for semantic fallback.
        text: Raw user question.

    Returns:
        Parsed semantic fallback result.
    """
    messages = [
        ChatMessage(role="system", content=FALLBACK_CLASSIFIER_SYSTEM_PROMPT),
        ChatMessage(role="user", content=text),
    ]
    response = await provider.chat(
        messages,
        model=provider.dialog_recorder_model,
        max_tokens=FALLBACK_MAX_TOKENS,
        temperature=FALLBACK_TEMPERATURE,
        enable_thinking=False,
    )
    return parse_fallback_classification(response.content)


def build_fallback_classifier_messages(text: str) -> list[dict[str, str]]:
    """Build chat messages for the generic fallback classifier call.

    Args:
        text: Raw user question.

    Returns:
        Two-message chat prompt for the fallback classifier.
    """
    return [
        {"role": "system", "content": FALLBACK_CLASSIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]


async def classify_memory_question_with_llm_call(
    llm_call: object,
    text: str,
) -> SemanticFallbackResult:
    """Classify a low-confidence question through a generic LLM call.

    Args:
        llm_call: Async callable that accepts chat messages.
        text: Raw user question.

    Returns:
        Parsed semantic fallback result.
    """
    response = await llm_call(build_fallback_classifier_messages(text))
    raw_output = getattr(response, "text", "")
    return parse_fallback_classification(raw_output)
