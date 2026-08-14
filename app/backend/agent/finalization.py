"""Response requirements and final-answer acceptance for chat orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING

from backend.agent.turn_state import (
    ObligationStatus,
    SatisfactionBind,
    ToolObligation,
)


if TYPE_CHECKING:
    from backend.llm.structured_response import StructuredAssistantContent


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
class ResponseRequirements:
    """Internal requirements that must be satisfied before final answer."""

    needs_current_data: bool = False
    needs_url_fetch: bool = False
    needs_file_context: bool = False
    needs_memory_recall: bool = False
    required_tools: tuple[str, ...] = ()
    # Tools the model MUST use (user-forced or model-declared) — forced on reask
    # in ANY trust zone, unlike context-only required_tools.
    must_use_tools: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    reason: str = ""
    invocations: tuple[InvocationRequirement, ...] = ()


class RequirementStatus(Enum):
    """Status of a single required evidence invocation."""

    PENDING = "pending"
    SATISFIED = "satisfied"
    FAILED = "failed"


@dataclass(frozen=True)
class InvocationRequirement:
    """One required evidence invocation with identity and state.

    Each instance is created when a URL or target is detected in the
    user message and is later marked satisfied or failed as the
    gateway processes tool invocations.
    """

    key: str
    description: str
    status: RequirementStatus = RequirementStatus.PENDING
    error: str | None = None


@dataclass(frozen=True)
class RequirementSatisfaction:
    """Tools/evidence already satisfied for one turn."""

    satisfied_tools: frozenset[str] = frozenset()
    failed_tools: dict[str, str] = field(default_factory=dict)
    current_data_targets: tuple[str, ...] = ()
    missing_current_data_targets: tuple[str, ...] = ()
    invocations: tuple[InvocationRequirement, ...] = ()

    def with_success(self, tool_name: str) -> RequirementSatisfaction:
        """Return a copy with one tool marked successful."""
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools | {tool_name},
            failed_tools=dict(self.failed_tools),
            current_data_targets=self.current_data_targets,
            missing_current_data_targets=self.missing_current_data_targets,
            invocations=self.invocations,
        )

    def with_failure(self, tool_name: str, reason: str) -> RequirementSatisfaction:
        """Return a copy with one tool failure recorded."""
        failed_tools = dict(self.failed_tools)
        failed_tools[tool_name] = reason
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools,
            failed_tools=failed_tools,
            current_data_targets=self.current_data_targets,
            missing_current_data_targets=self.missing_current_data_targets,
            invocations=self.invocations,
        )

    def with_current_data_targets(
        self,
        *,
        targets: tuple[str, ...],
        missing_targets: tuple[str, ...] = (),
    ) -> RequirementSatisfaction:
        """Return a copy with current-data evidence coverage details."""
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools,
            failed_tools=dict(self.failed_tools),
            current_data_targets=targets,
            missing_current_data_targets=missing_targets,
            invocations=self.invocations,
        )

    def with_current_data_targets_covered(
        self,
        covered: tuple[str, ...],
    ) -> RequirementSatisfaction:
        """Return a copy with covered targets dropped from the missing set.

        A target becomes covered when some tool this turn actually fetched
        content naming it, even if the required web_search did not.
        """
        covered_folded = {label.casefold() for label in covered}
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools,
            failed_tools=dict(self.failed_tools),
            current_data_targets=self.current_data_targets,
            missing_current_data_targets=tuple(
                target
                for target in self.missing_current_data_targets
                if target.casefold() not in covered_folded
            ),
            invocations=self.invocations,
        )

    def with_invocation_status(
        self,
        key: str,
        status: RequirementStatus,
        error: str | None = None,
    ) -> RequirementSatisfaction:
        """Return a copy with one invocation's status updated."""
        updated = tuple(
            replace(inv, status=status, error=error) if inv.key == key else inv
            for inv in self.invocations
        )
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools,
            failed_tools=dict(self.failed_tools),
            current_data_targets=self.current_data_targets,
            missing_current_data_targets=self.missing_current_data_targets,
            invocations=updated,
        )

    def add_invocation(
        self,
        key: str,
        description: str,
    ) -> RequirementSatisfaction:
        """Return a copy with one new pending invocation appended."""
        return RequirementSatisfaction(
            satisfied_tools=self.satisfied_tools,
            failed_tools=dict(self.failed_tools),
            current_data_targets=self.current_data_targets,
            missing_current_data_targets=self.missing_current_data_targets,
            invocations=(*self.invocations, InvocationRequirement(key, description)),
        )

    def with_execution_records(
        self,
        records: list[dict[str, object]],
    ) -> RequirementSatisfaction:
        """Return a copy reconciled with successful and failed tool records."""
        satisfied = set(self.satisfied_tools)
        failed = dict(self.failed_tools)
        for record in records:
            name = str(record.get("name", "")).strip()
            if not name:
                continue
            status = str(record.get("status", ""))
            success = record.get("success") is True or status == "succeeded"
            if success:
                satisfied.add(name)
                failed.pop(name, None)
            elif status in {"blocked", "failed"}:
                failed[name] = str(record.get("error", status))
        return replace(
            self,
            satisfied_tools=frozenset(satisfied),
            failed_tools=failed,
        )


@dataclass(frozen=True)
class FinalAnswerDecision:
    """Decision made by the backend finalization gate."""

    accepted: bool
    reason: str
    requires_recovery: bool = False


def build_required_tool_failure_answer(
    *,
    requirements: ResponseRequirements,
    satisfaction: RequirementSatisfaction,
) -> str:
    """Build a deterministic terminal answer for failed mandatory evidence."""
    failed_required_tools = [
        tool
        for tool in requirements.required_tools
        if tool in satisfaction.failed_tools
    ]
    incomplete_invocations = [
        invocation
        for invocation in satisfaction.invocations
        if invocation.status is not RequirementStatus.SATISFIED
    ]
    missing_required_tools = [
        tool
        for tool in requirements.required_tools
        if tool not in satisfaction.satisfied_tools
        and tool not in satisfaction.failed_tools
    ]
    if (
        not failed_required_tools
        and not missing_required_tools
        and not incomplete_invocations
    ):
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

    tool_names = ", ".join((*failed_required_tools, *missing_required_tools))
    invocation_keys = ", ".join(item.key for item in incomplete_invocations)
    details = "; ".join(
        part
        for part in (
            f"tools: {tool_names}" if tool_names else "",
            f"invocations: {invocation_keys}" if invocation_keys else "",
        )
        if part
    )
    return f"I could not complete the required tool evidence ({details})."


def requirements_with_expected_tools(
    base: ResponseRequirements,
    forced_tools: tuple[str, ...] | list[str] = (),
    declared_tool: str | None = None,
) -> ResponseRequirements:
    """Fold user-forced and model-declared tools into a requirements copy.

    The added tools go into both ``required_tools`` and ``must_use_tools`` so
    the durable evidence gate and exact reask use the same explicit authority.
    """
    extra = [tool for tool in forced_tools if tool]
    if declared_tool:
        extra.append(declared_tool)
    if not extra:
        return base
    required = tuple(dict.fromkeys((*base.required_tools, *extra)))
    must_use = tuple(dict.fromkeys((*base.must_use_tools, *extra)))
    return replace(base, required_tools=required, must_use_tools=must_use)


def build_tool_obligations(
    *,
    run_id: str,
    requirements: ResponseRequirements,
) -> tuple[ToolObligation, ...]:
    """Create stable concrete evidence obligations for an enabled turn."""
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"bimemo-agent-turn:{run_id}")
    obligations: list[ToolObligation] = []
    ordinal = 0

    def append(
        tool_name: str,
        *,
        source: str,
        evidence_key: str,
        argument_predicate: dict[str, object] | None = None,
    ) -> None:
        nonlocal ordinal
        ordinal += 1
        obligation_id = str(
            uuid.uuid5(
                namespace,
                f"{ordinal}:{tool_name}:{evidence_key}",
            )
        )
        obligations.append(
            ToolObligation(
                run_id=run_id,
                obligation_id=obligation_id,
                required_tool=tool_name,
                source=source,
                evidence_key=evidence_key,
                argument_predicate=argument_predicate,
            )
        )

    for tool_name in requirements.required_tools:
        if tool_name == "http_fetch" and requirements.urls:
            continue
        append(
            tool_name,
            source="explicit" if tool_name in requirements.must_use_tools else "route",
            evidence_key=f"tool:{tool_name}",
        )
    for index, url in enumerate(requirements.urls, start=1):
        append(
            "http_fetch",
            source="url",
            evidence_key=f"url:{index}:{url}",
            argument_predicate={"url": url},
        )
    hinted_tools: list[tuple[bool, str, str]] = [
        (requirements.needs_file_context, "file_read", "file-context"),
        (requirements.needs_memory_recall, "memory_recall", "memory-recall"),
    ]
    existing = {(item.required_tool, item.evidence_key) for item in obligations}
    for enabled, tool_name, evidence_key in hinted_tools:
        if enabled and (tool_name, evidence_key) not in existing:
            append(
                tool_name,
                source="route-hint",
                evidence_key=evidence_key,
            )
    return tuple(obligations)


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
    requirements: ResponseRequirements,
    satisfaction: RequirementSatisfaction,
    obligations: tuple[ToolObligation, ...] | None = None,
    binds: tuple[SatisfactionBind, ...] = (),
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
    if obligations is not None:
        bound_obligation_ids = {bind.obligation_id for bind in binds}
        missing_obligations = [
            obligation.obligation_id
            for obligation in obligations
            if obligation.status is not ObligationStatus.SATISFIED
            or obligation.obligation_id not in bound_obligation_ids
        ]
        if missing_obligations:
            return FinalAnswerDecision(
                accepted=False,
                reason=(
                    f"missing-required-obligations:{','.join(missing_obligations)}"
                ),
                requires_recovery=True,
            )
        return FinalAnswerDecision(accepted=True, reason="accepted")
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
    failed_tools = [
        tool
        for tool in requirements.required_tools
        if tool in satisfaction.failed_tools
    ]
    if failed_tools:
        return FinalAnswerDecision(
            accepted=False,
            reason=f"failed-required-tools:{','.join(failed_tools)}",
            requires_recovery=False,
        )
    incomplete_invocations = [
        invocation
        for invocation in satisfaction.invocations
        if invocation.status is not RequirementStatus.SATISFIED
    ]
    if incomplete_invocations:
        keys = ",".join(invocation.key for invocation in incomplete_invocations)
        return FinalAnswerDecision(
            accepted=False,
            reason=f"incomplete-required-invocations:{keys}",
            requires_recovery=False,
        )
    if requirements.needs_current_data and satisfaction.missing_current_data_targets:
        normalized_answer = " ".join(answer_text.casefold().split())
        missing_markers = (
            "not found",
            "could not find",
            "not available",
            "no evidence",
            "не найден",
            "не удалось",
            "нет данных",
            "данные отсутств",
        )
        missing_targets_without_notice = [
            target
            for target in satisfaction.missing_current_data_targets
            if target.casefold() not in normalized_answer
            or not any(marker in normalized_answer for marker in missing_markers)
        ]
        if missing_targets_without_notice:
            return FinalAnswerDecision(
                accepted=False,
                reason=(
                    "missing-current-data-targets:"
                    f"{','.join(missing_targets_without_notice)}"
                ),
                requires_recovery=True,
            )
    return FinalAnswerDecision(accepted=True, reason="accepted")
