"""Turn requirements and final-answer acceptance for chat orchestration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    message_allows_file_read_tools,
    message_may_need_memory_recall,
)


if TYPE_CHECKING:
    from backend.llm.structured_response import StructuredAssistantContent


URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")
CURRENT_DATA_MARKERS = (
    "актуальн",
    "данный момент",
    "сейчас",
    "сегодня",
    "текущ",
    "курс",
    "курсы",
    "цена",
    "цены",
    "новост",
    "current",
    "latest",
    "now",
    "today",
    "price",
    "prices",
    "news",
)
URL_MARKERS = ("http://", "https://")
STATUS_INTENT_MARKERS = (
    "search",
    "look up",
    "looking up",
    "check",
    "checking",
    "find",
    "fetch",
    "поищ",
    "ищу",
    "найд",
    "провер",
    "посмотр",
    "узна",
)
RESULT_MARKERS = (
    "$",
    "€",
    "₽",
    "%",
    ":",
    "-",
    "—",
    "is ",
    "are ",
    "состав",
    "рав",
    "найден",
    "данн",
)


@dataclass(frozen=True)
class TurnRequirements:
    """Internal requirements that must be satisfied before final answer."""

    needs_current_data: bool = False
    needs_url_fetch: bool = False
    needs_file_context: bool = False
    needs_memory_recall: bool = False
    required_tools: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class RequirementSatisfaction:
    """Tools/evidence already satisfied for one turn."""

    satisfied_tools: frozenset[str] = frozenset()
    failed_tools: dict[str, str] = field(default_factory=dict)

    def with_success(self, tool_name: str) -> RequirementSatisfaction:
        """Return a copy with one tool marked successful."""
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools | {tool_name},
            failed_tools=dict(self.failed_tools),
        )

    def with_failure(self, tool_name: str, reason: str) -> RequirementSatisfaction:
        """Return a copy with one tool failure recorded."""
        failed_tools = dict(self.failed_tools)
        failed_tools[tool_name] = reason
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools,
            failed_tools=failed_tools,
        )


@dataclass(frozen=True)
class FinalAnswerDecision:
    """Decision made by the backend finalization gate."""

    accepted: bool
    reason: str
    requires_recovery: bool = False


def build_required_tool_failure_answer(
    *,
    requirements: TurnRequirements,
    satisfaction: RequirementSatisfaction,
) -> str:
    """Build a deterministic terminal answer for failed mandatory evidence."""
    failed_required_tools = [
        tool
        for tool in requirements.required_tools
        if tool in satisfaction.failed_tools
    ]
    if not failed_required_tools:
        return ""

    if "web_search" in failed_required_tools and requirements.needs_current_data:
        detail = satisfaction.failed_tools.get("web_search", "").strip()
        if detail:
            return (
                "I could not fetch current data through web_search, so I cannot "
                f"answer this request reliably. Reason: {detail}"
            )
        return (
            "I could not fetch current data through web_search, so I cannot "
            "answer this request reliably."
        )

    tool_names = ", ".join(failed_required_tools)
    return (
        "I could not fetch required evidence for this answer through: "
        f"{tool_names}."
    )


def detect_turn_requirements(message: str) -> TurnRequirements:
    """Detect mandatory evidence requirements for one user message."""
    normalized = " ".join(message.casefold().split())
    urls = tuple(_extract_urls(message))
    needs_current_data = any(marker in normalized for marker in CURRENT_DATA_MARKERS)
    needs_url_fetch = bool(urls) or any(marker in normalized for marker in URL_MARKERS)
    needs_file_context = message_allows_file_read_tools(message)
    needs_memory_recall = message_may_need_memory_recall(message)
    required_tools: list[str] = []
    reasons: list[str] = []
    if needs_current_data:
        required_tools.append("web_search")
        reasons.append("current-data")
    if needs_url_fetch:
        required_tools.append("http_fetch")
        reasons.append("url-fetch")
    if needs_file_context:
        reasons.append("file-context")
    if needs_memory_recall:
        reasons.append("memory-recall")
    return TurnRequirements(
        needs_current_data=needs_current_data,
        needs_url_fetch=needs_url_fetch,
        needs_file_context=needs_file_context,
        needs_memory_recall=needs_memory_recall,
        required_tools=tuple(dict.fromkeys(required_tools)),
        urls=urls,
        reason=",".join(reasons),
    )


def _extract_urls(message: str) -> list[str]:
    """Return normalized explicit URLs from one user message."""
    urls: list[str] = []
    for match in URL_PATTERN.finditer(message):
        url = match.group(0).rstrip(".,;:!?)]}")
        if url:
            urls.append(url)
    return urls


def _answer_text(
    raw_final_text: str,
    structured_content: StructuredAssistantContent | None,
) -> str:
    if structured_content is not None and structured_content.answer.strip():
        return structured_content.answer
    return raw_final_text


def looks_like_status_only_answer(answer_text: str) -> bool:
    """Return True when text describes future work instead of a result."""
    normalized = " ".join(answer_text.casefold().split())
    if not normalized or len(normalized) > 260:
        return False
    has_status_intent = any(marker in normalized for marker in STATUS_INTENT_MARKERS)
    if not has_status_intent:
        return False
    has_result_marker = any(marker in normalized for marker in RESULT_MARKERS)
    return not has_result_marker


def validate_final_answer(
    *,
    raw_final_text: str,
    structured_content: StructuredAssistantContent | None,
    requirements: TurnRequirements,
    satisfaction: RequirementSatisfaction,
) -> FinalAnswerDecision:
    """Decide whether one model output is acceptable as final."""
    answer_text = _answer_text(raw_final_text, structured_content).strip()
    if not answer_text:
        return FinalAnswerDecision(
            accepted=False,
            reason="empty-answer",
            requires_recovery=True,
        )
    if looks_like_status_only_answer(answer_text):
        return FinalAnswerDecision(
            accepted=False,
            reason="status-only-answer",
            requires_recovery=True,
        )
    missing_tools = [
        tool
        for tool in requirements.required_tools
        if tool not in satisfaction.satisfied_tools
        and tool not in satisfaction.failed_tools
    ]
    if missing_tools:
        return FinalAnswerDecision(
            accepted=False,
            reason=f"missing-required-tools:{','.join(missing_tools)}",
            requires_recovery=True,
        )
    return FinalAnswerDecision(accepted=True, reason="accepted")
