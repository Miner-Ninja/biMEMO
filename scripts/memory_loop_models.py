"""Dataclasses for the memory loop evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ChatDialogStepResult:
    """Store one dialog step and the API response used for evaluation."""

    prompt: str
    response_text: str
    elapsed_seconds: float
    retrieved_memories: list[dict[str, Any]]
    retrieved_messages: list[dict[str, Any]]
    error: str | None = None


@dataclass(slots=True)
class ScenarioState:
    """Hold the memory state derived from one scenario conversation."""

    conversation_id: str
    messages: list[dict[str, Any]]
    records: list[dict[str, Any]]
    summaries: list[dict[str, Any]]
    quality_report: dict[str, Any]
    unreflected_record_count: int = 0
    scenario_unreflected_record_count: int = 0
    covered_message_count: int = 0
    message_coverage: float = 0.0
    first_record_elapsed_seconds: float | None = None
    stabilization_elapsed_seconds: float = 0.0
    duplication_report: dict[str, int] | None = None
    contamination_report: dict[str, float] | None = None


@dataclass(slots=True)
class EvaluationFinding:
    """Single evaluation finding with optional recommendation."""

    name: str
    passed: bool
    detail: str
    recommendation: str = ""


@dataclass(slots=True)
class PhaseSummary:
    """Compact diagnostics for one conversation phase."""

    dialog_step_count: int
    avg_latency_seconds: float
    avg_retrieved_memories: float
    avg_retrieved_messages: float
    current_style_response_rate: float
    old_style_only_response_rate: float
    error_count: int = 0


@dataclass(slots=True)
class ScenarioReport:
    """Full report for one scenario iteration."""

    scenario_tag: str
    conversation_id: str
    passed: bool
    findings: list[EvaluationFinding]
    recommendations: list[str]
    dialog_step_results: list[ChatDialogStepResult]
    quality_score: int
    scenario_counts: dict[str, int]
    phase_breakdown: dict[str, PhaseSummary]
    duplication_report: dict[str, int]
    contamination_report: dict[str, float] = field(default_factory=dict)
    retrieval_report: dict[str, Any] = field(default_factory=dict)
    first_record_elapsed_seconds: float | None = None
    stabilization_elapsed_seconds: float = 0.0
