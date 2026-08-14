"""Typed state for durable agent turns and tool evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AuthorityStore(StrEnum):
    """Database that owns one turn for its complete lifetime."""

    CORE = "core"
    RUNTIME = "runtime"


class TurnArchitecture(StrEnum):
    """Pinned lifecycle implementation selected when a turn is created."""

    LEGACY = "legacy"
    STRUCTURED_V1 = "structured_v1"


class TurnOutcomeKind(StrEnum):
    """Closed set of externally observable turn outcomes."""

    FINAL = "final"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ObligationStatus(StrEnum):
    """Lifecycle of mandatory evidence."""

    OPEN = "open"
    SATISFIED = "satisfied"
    CLOSED = "closed"


class InvocationStatus(StrEnum):
    """Lifecycle of a concrete native tool call."""

    REQUIRED = "required"
    PENDING_CONFIRMATION = "pending_confirmation"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class ConfirmationStatus(StrEnum):
    """Canonical decision state for a confirmation request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class PendingInputStatus(StrEnum):
    """Lifecycle of one typed missing-input request."""

    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"


class ClarificationStatus(StrEnum):
    """State of a low-confidence follow-up proposal."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


def canonical_arguments(arguments: dict[str, Any]) -> str:
    """Serialize arguments deterministically for identity and replay checks."""
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def arguments_hash(arguments: dict[str, Any]) -> str:
    """Return a stable SHA-256 identity for canonical arguments."""
    return hashlib.sha256(canonical_arguments(arguments).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolObligation:
    """Required evidence known independently of a concrete invocation."""

    run_id: str
    obligation_id: str
    required_tool: str
    source: str
    evidence_key: str
    argument_predicate: dict[str, Any] | None = None
    status: ObligationStatus = ObligationStatus.OPEN
    bound_invocation_ids: tuple[str, ...] = ()

    def accepts(self, invocation: ToolInvocation) -> bool:
        """Return whether an invocation is eligible to satisfy this obligation."""
        if (
            invocation.run_id != self.run_id
            or invocation.tool_name != self.required_tool
        ):
            return False
        if invocation.status is not InvocationStatus.SUCCEEDED:
            return False
        if self.argument_predicate is None:
            return True
        return all(
            invocation.arguments.get(key) == value
            for key, value in self.argument_predicate.items()
        )


@dataclass(frozen=True)
class ToolInvocation:
    """One concrete native tool-call attempt."""

    run_id: str
    invocation_id: str
    batch_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    canonical_args_hash: str
    status: InvocationStatus = InvocationStatus.REQUIRED
    source: str = "model"
    evidence_key: str | None = None
    idempotency_key: str | None = None
    claim_generation: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    confirmation_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        invocation_id: str,
        batch_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        **values: Any,  # noqa: ANN401
    ) -> ToolInvocation:
        """Build an invocation with a backend-derived argument identity."""
        return cls(
            run_id=run_id,
            invocation_id=invocation_id,
            batch_id=batch_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            canonical_args_hash=arguments_hash(arguments),
            **values,
        )


@dataclass(frozen=True)
class CandidateAssociation:
    """Non-satisfying candidate link between obligation and invocation."""

    obligation_id: str
    invocation_id: str


@dataclass(frozen=True)
class SatisfactionBind:
    """Immutable successful evidence binding."""

    obligation_id: str
    invocation_id: str
    evidence_key: str


def compatible_obligations(
    invocation: ToolInvocation,
    obligations: list[ToolObligation],
) -> tuple[ToolObligation, ...]:
    """Select obligations one success may bind under R3 cardinality rules."""
    matches = [item for item in obligations if item.accepts(invocation)]
    if not matches:
        return ()
    first = matches[0]
    if not first.evidence_key:
        return (first,)
    shared = tuple(item for item in matches if item.evidence_key == first.evidence_key)
    return shared or (first,)


@dataclass(frozen=True)
class ToolCallBatch:
    """One assistant native-tool message and all sibling call identities."""

    run_id: str
    batch_id: str
    assistant_message: dict[str, Any]
    invocation_ids: tuple[str, ...]


@dataclass(frozen=True)
class PendingConfirmation:
    """Canonical confirmation card backed by one invocation."""

    run_id: str
    confirmation_id: str
    invocation_id: str
    status: ConfirmationStatus
    expires_at: str
    title: str
    description: str
    tool_name: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingInput:
    """One typed value required to resume the same semantic plan."""

    run_id: str
    pending_input_id: str
    question: str
    expected_input_schema: dict[str, Any]
    status: PendingInputStatus
    expires_at: str
    cas_version: int = 1
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClarificationPending:
    """Durable low-confidence proposal that is not tool evidence."""

    run_id: str
    clarification_id: str
    proposed_tool: str
    reason: str
    status: ClarificationStatus
    expires_at: str


@dataclass(frozen=True)
class TurnCounters:
    """Persisted foreground budget consumption."""

    active_time_ms: int = 0
    provider_calls: int = 0
    tool_rounds: int = 0
    enforcement_reasks: int = 0
    synthesis_attempts: int = 0
    semantic_rounds: int = 0


@dataclass(frozen=True)
class TerminalError:
    """Typed user-safe terminal failure."""

    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class TurnOutcome:
    """Canonical internal result with outcome-specific field validation."""

    kind: TurnOutcomeKind
    run_id: str
    finalization_id: str | None = None
    structured_content: Any | None = None
    confirmations: tuple[PendingConfirmation, ...] = ()
    pending_input: PendingInput | None = None
    error: TerminalError | None = None
    assistant_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate fields allowed for the selected outcome kind."""
        if self.kind is TurnOutcomeKind.FINAL:
            if self.finalization_id is None or self.structured_content is None:
                raise ValueError(
                    "FINAL requires finalization_id and structured_content"
                )
            if (
                self.confirmations
                or self.pending_input is not None
                or self.error is not None
            ):
                raise ValueError("FINAL forbids confirmations and terminal error")
        elif self.kind is TurnOutcomeKind.PAUSED:
            if bool(self.confirmations) == bool(self.pending_input):
                raise ValueError(
                    "PAUSED requires exactly one of confirmations or pending input"
                )
            if self.structured_content is not None or self.finalization_id is not None:
                raise ValueError(
                    "PAUSED forbids structured content and finalization_id"
                )
        elif self.kind is TurnOutcomeKind.FAILED:
            if self.error is None:
                raise ValueError("FAILED requires a typed error")
            if self.confirmations or self.pending_input is not None:
                raise ValueError("FAILED forbids pending state")
        elif self.kind is TurnOutcomeKind.CANCELLED:
            if (
                any(
                    value is not None
                    for value in (
                        self.finalization_id,
                        self.structured_content,
                        self.error,
                        self.assistant_message_id,
                    )
                )
                or self.confirmations
                or self.pending_input is not None
            ):
                raise ValueError("CANCELLED carries no final payload")


__all__ = [
    "AuthorityStore",
    "CandidateAssociation",
    "ClarificationPending",
    "ClarificationStatus",
    "ConfirmationStatus",
    "InvocationStatus",
    "ObligationStatus",
    "PendingConfirmation",
    "PendingInput",
    "PendingInputStatus",
    "SatisfactionBind",
    "TerminalError",
    "ToolCallBatch",
    "ToolInvocation",
    "ToolObligation",
    "TurnArchitecture",
    "TurnCounters",
    "TurnOutcome",
    "TurnOutcomeKind",
    "arguments_hash",
    "canonical_arguments",
    "compatible_obligations",
]
