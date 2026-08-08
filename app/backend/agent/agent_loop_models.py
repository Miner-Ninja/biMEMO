"""Data models for the gateway agent loop.

Contains all dataclasses used by the gateway orchestrator and its
helpers: tool calls, tool results, gateway responses, turn state,
and memory routing decisions. These are pure data containers with
no behavior or side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.agent.finalization import RequirementSatisfaction, ResponseRequirements
    from backend.agent.loop import LoopResult
    from backend.llm.structured_response import StructuredAssistantContent
    from backend.llm.usage import LLMCallUsage
    from backend.memory.classification.question_router import MemoryQuestionRoute
    from backend.memory.classification.trace import MemoryQuestionRouteTrace
    from backend.models.chat_stats import ExtendedUsage


@dataclass(frozen=True)
class ToolCall:
    """A tool call from the LLM via native function calling.

    Attributes:
        id: API-assigned call ID for result correlation.
        name: Tool name.
        parameters: Tool parameters.
    """

    id: str
    name: str
    parameters: dict


@dataclass(frozen=True)
class AgentResponse:
    """Response from the gateway orchestrator.

    Attributes:
        structured_content: Final visible answer and optional extra sections.
        loop_result: Agentic loop result.
        input_filtered: True if input had issues.
        output_filtered: True if output was redacted.
        retrieved_memories: Summaries of search-matched records.
        retrieved_messages: Debug metadata for FTS-matched messages.
        assistant_message_id: DB id of the persisted final assistant message
            (None when no message was persisted, e.g. a pending-confirmation
            turn). Used by the scheduler to forward a task result through the
            conversation's bridge under its real message id.
    """

    structured_content: StructuredAssistantContent
    loop_result: LoopResult
    input_filtered: bool = False
    output_filtered: bool = False
    retrieved_memories: list[dict[str, str]] = field(default_factory=list)
    retrieved_messages: list[dict[str, str]] | None = None
    stats: ExtendedUsage | None = None
    assistant_message_id: str | None = None


@dataclass(frozen=True)
class PreparedContext:
    """Prepared conversation context for one orchestrator turn."""

    entries: list[object]
    retrieved_memories: list[dict[str, str]]
    retrieved_messages: list[dict[str, str]] | None
    memory_guidance: str | None
    zone_usage: dict[str, int] | None = None
    zone_budget: dict[str, int] | None = None


@dataclass(frozen=True)
class ToolRoundResult:
    """Result of one model tool round."""

    raw_final_text: str
    structured_content: StructuredAssistantContent | None
    executed_immediate_tools: bool
    should_continue: bool


@dataclass(frozen=True)
class ModelLoopResult:
    """Final model-loop output before assistant-response post-processing."""

    raw_final_text: str
    structured_content: StructuredAssistantContent | None = None


@dataclass(frozen=True)
class MemoryRouteDecision:
    """Final memory-routing choice for one user turn."""

    route: MemoryQuestionRoute
    trace: MemoryQuestionRouteTrace


@dataclass(frozen=True)
class TurnPreparation:
    """Everything needed to execute one prepared user turn."""

    filtered_message: str
    input_filtered: bool
    request_started_at: float
    call_usages: list[LLMCallUsage]
    prepared_context: PreparedContext
    user_message_id: str | None
    messages: list[dict[str, object]]
    openai_tools: list[dict] | None
    question_route: MemoryQuestionRoute
    requirements: ResponseRequirements
    satisfaction: RequirementSatisfaction


@dataclass(frozen=True)
class TurnInputState:
    """Filtered user input and uploaded media for one prepared turn."""

    filtered_message: str
    input_filtered: bool
    uploaded_images: list[str]


@dataclass(frozen=True)
class FinalizedAssistantResponse:
    """Final assistant reply after output filtering and usage aggregation.

    ``response_text`` is the final reply text shown to the user after output
    filtering. It is the filtered form of the lower-layer ``raw_final_text``.
    ``structured_content.answer`` should match this final reply text.
    """

    loop_result: LoopResult
    response_text: str
    output_filtered: bool
    stats: ExtendedUsage | None
    structured_content: StructuredAssistantContent | None = None
