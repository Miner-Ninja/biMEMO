"""Gateway orchestrator for the agentic loop.

Ties together LLM calls, native tool calling, zone-based
execution, and result feedback into a single loop.
Uses asyncio.gather for parallel tool execution.

Both sync and async context_fn/save_fn are supported so the
gateway can call the orchestrator helpers without caring about
their storage transport details.

Skill subsystem and the structured confirmation state machine are
implemented in companion ``agent_loop_skills`` and
``agent_loop_confirmation`` modules and delegated here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field, replace
from time import perf_counter
from typing import TYPE_CHECKING

from backend.agent import agent_loop_confirmation as confirm_ops
from backend.agent import agent_loop_skills as skill_ops
from backend.agent.agent_loop_confirmation import _semantic_tool_choice
from backend.agent.agent_loop_models import (
    AgentResponse,
    FinalizedAssistantResponse,
    PreparedContext,
    TurnInputState,
    TurnPreparation,
)
from backend.agent.agent_loop_skills import (
    SKILL_QUALITY_AXIS_MAX,
    SKILL_QUALITY_MIN_PER_AXIS,
    SKILL_QUALITY_MIN_TOTAL,
    _appraise_skill_quality,
)
from backend.agent.finalization import (
    RequirementSatisfaction,
    ResponseRequirements,
    build_tool_obligations,
    requirements_with_expected_tools,
)
from backend.agent.llm_caller import LLMCaller
from backend.agent.loop import (
    AgenticLoop,
)
from backend.agent.memory_context import MemoryContextLoader
from backend.agent.message_persistence import MessagePersistence
from backend.agent.prompt_builder import PromptBuilder
from backend.agent.semantic_planning import (
    with_semantic_controls,
)
from backend.agent.stage_timings import StageTimings
from backend.agent.tool_executor import ToolExecutor
from backend.agent.turn_state import (
    ObligationStatus,
    TerminalError,
    ToolObligation,
    TurnOutcomeKind,
)
from backend.constants import TOOL_POLICY_VERSION
from backend.ipc.models import build_multimodal_content
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.structured_response import (
    StructuredAssistantContent,
)
from backend.llm.usage import aggregate_call_usages
from backend.memory.classification.question_router import route_memory_question
from backend.memory.classification.resolver import (
    LongTermMemoryResolver,
)
from backend.permissions import (
    ActionDecisionService,
)
from backend.security.content_filter import (
    filter_input,
    filter_output,
)
from backend.security.policy import get_policy
from backend.tools.rate_limits import ToolRateLimiter
from backend.trust.confirmation import (
    ConfirmationManager,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.agent_loop_skills import SkillQualityVerdict
    from backend.agent.concurrency import AgentConcurrency
    from backend.llm.call_factory import AttemptRecord
    from backend.llm.usage import LLMCallUsage
    from backend.tools.broker import ToolBroker
    from backend.trust.confirmation import PendingAction


logger = logging.getLogger(__name__)


# Re-export for backward compatibility (used by tests)
__all__ = [
    "SKILL_QUALITY_AXIS_MAX",
    "SKILL_QUALITY_MIN_PER_AXIS",
    "SKILL_QUALITY_MIN_TOTAL",
    "AgentOrchestrator",
    "_appraise_skill_quality",
    "_semantic_tool_choice",
]


def _require_structured_content(
    structured_content: StructuredAssistantContent | None,
    response_text: str,
) -> StructuredAssistantContent:
    """Return the structured answer or build one from visible text."""
    if structured_content is not None:
        return structured_content
    return StructuredAssistantContent(answer=response_text)


@dataclass
class AgentOrchestrator:
    """Orchestrates the agentic loop.

    Coordinates LLM calls, tool execution, content
    filtering, and memory context loading.

    Skill subsystem and the structured confirmation state
    machine are implemented in companion ``agent_loop_skills``
    and ``agent_loop_confirmation`` modules and delegated here.

    Attributes:
        broker: Tool broker for execution.
        concurrency: Cross-channel concurrency control.
        context_fn: Callable returning context entries.
    """

    broker: ToolBroker
    concurrency: AgentConcurrency
    context_fn: Callable
    evidence_bundle_fn: Callable | None = None
    graph_facts_fn: Callable | None = None
    save_fn: Callable | None = None
    save_record_fn: Callable | None = None
    write_memory_route_diagnostic_fn: Callable | None = None
    notify_primary_started: Callable[[], None] | None = None
    notify_primary_finished: Callable[[], None] | None = None
    confirmation_mgr: ConfirmationManager = field(default_factory=ConfirmationManager)
    stream_queue: object | None = None
    user_profile: dict[str, str] = field(default_factory=dict)
    model_name: str = ""
    list_skills_fn: Callable | None = None
    search_skills_fn: Callable | None = None
    create_skill_fn: Callable | None = None
    log_skill_usage_fn: Callable | None = None
    turn_store: object | None = None
    run_event_recorder: object | None = None
    scheduled_task_run: dict[str, object] | None = None
    _current_run_id: str = field(default="")
    MIN_COMPLETION_TOKENS_FOR_SKILL = 300

    def __post_init__(self) -> None:
        """Initialize delegate objects after dataclass construction."""
        action_decisions = ActionDecisionService(
            registry=self.broker.registry,
            workspace_roots=self.broker.workspace_roots,
        )
        self.tool_executor = ToolExecutor(
            self.broker,
            self.confirmation_mgr,
            action_decisions=action_decisions,
            turn_store=self.turn_store,
        )
        # Per-turn accumulator for the processing-stage timing breakdown; reset
        # at the start of each turn (see process_message) and read at finalize.
        self._stage_timings = StageTimings()
        self.tool_executor.stage_timings = self._stage_timings
        # Tools the user forced for the current turn (from the chat-input chips).
        # Set at the start of process_message; read when building the model call.
        self._forced_tools: list[str] = []
        self.llm_caller = LLMCaller(
            self.model_name,
            self.broker,
        )
        self._provider_attempt_tasks: list[asyncio.Task] = []
        self.llm_caller.on_attempt = self._record_provider_attempt
        self.prompt_builder = PromptBuilder(
            self.model_name,
            self.user_profile,
            self.broker,
            self.evidence_bundle_fn,
        )
        self.memory_loader = MemoryContextLoader(
            self.context_fn,
            self.evidence_bundle_fn,
            self.write_memory_route_diagnostic_fn,
        )
        self.persistence = MessagePersistence(
            self.save_fn,
            self.save_record_fn,
        )
        self.tool_executor.stream_queue = self.stream_queue
        self.llm_caller.stream_queue = self.stream_queue

    @property
    def current_run_id(self) -> str:
        """Return the run id of the turn this process is executing now."""
        return self._current_run_id

    def __setattr__(self, name: str, value: object) -> None:
        """Sync delegate state when mutable attributes change post-init."""
        object.__setattr__(self, name, value)
        if name == "stream_queue":
            if hasattr(self, "tool_executor"):
                self.tool_executor.stream_queue = value
            if hasattr(self, "llm_caller"):
                self.llm_caller.stream_queue = value
        elif name == "save_fn" and hasattr(self, "persistence"):
            self.persistence.save_fn = value
        elif name == "save_record_fn" and hasattr(self, "persistence"):
            self.persistence.save_record_fn = value
        elif name == "context_fn" and hasattr(self, "memory_loader"):
            self.memory_loader.context_fn = value
        elif name == "model_name":
            if hasattr(self, "llm_caller"):
                self.llm_caller.model_name = value
            if hasattr(self, "prompt_builder"):
                self.prompt_builder.model_name = value

    async def _load_skill_index(self) -> None:
        """Load skill index into prompt builder if callback is available."""
        return await skill_ops.load_skill_index(self)

    async def _search_skills_for_message(self, message: str) -> str | None:
        """Search for a relevant skill before processing a message."""
        return await skill_ops.search_skills_for_message(self, message)

    async def _log_skill_usage(
        self,
        message: str,
        duration_ms: int,
    ) -> None:
        """Log usage for matched skills."""
        return await skill_ops.log_skill_usage(self, message, duration_ms)

    def _build_skill_name(self, message: str) -> str:
        """Build a kebab-case skill name from a user message."""
        return skill_ops._build_skill_name(self, message)

    async def _run_skill_quality_appraisal(
        self,
        *,
        title: str,
        description: str,
        trigger: str,
        procedure: str,
        errors: str,
        verification: str,
        llm_call: object,
    ) -> SkillQualityVerdict | None:
        """Run a separate LLM call to appraise skill quality."""
        return await skill_ops.run_skill_quality_appraisal(
            self,
            title=title,
            description=description,
            trigger=trigger,
            procedure=procedure,
            errors=errors,
            verification=verification,
            llm_call=llm_call,
        )

    def _schedule_skill_generation(
        self,
        message: str,
        tool_call_count: int,
        completion_tokens: int,
        llm_call: object,
    ) -> None:
        """Schedule background skill generation if task was non-trivial."""
        return skill_ops.schedule_skill_generation(
            self,
            message,
            tool_call_count,
            completion_tokens,
            llm_call,
        )

    def _log_background_skill_failure(self, task: asyncio.Task[None]) -> None:
        """Log one background skill-generation failure."""
        return skill_ops.log_background_skill_failure(self, task)

    async def _generate_and_save_skill(
        self,
        message: str,
        tool_call_count: int,
        llm_call: object,
    ) -> None:
        """Generate skill content via LLM and save to database."""
        return await skill_ops.generate_and_save_skill(
            self,
            message,
            tool_call_count,
            llm_call,
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt (delegates to prompt_builder)."""
        return self.prompt_builder.build_system_prompt()

    @property
    def _pending_red(self) -> list[PendingAction]:
        """Access pending red calls on the tool executor."""
        return self.tool_executor._pending_red

    @_pending_red.setter
    def _pending_red(self, value: list[PendingAction]) -> None:
        """Set pending red calls on the tool executor."""
        self.tool_executor._pending_red = value

    def _log_context_composition(
        self,
        context_entries: list[object],
    ) -> None:
        """Log the role breakdown of one loaded prompt context."""
        role_counts: dict[str, int] = {}
        for context_entry in context_entries:
            role_counts[context_entry.role] = role_counts.get(context_entry.role, 0) + 1
        logger.warning(
            "Context composition: %s (total=%d entries)",
            role_counts,
            len(context_entries),
        )

    def _finalize_assistant_response(
        self,
        *,
        raw_final_text: str,
        structured_content: StructuredAssistantContent | None,
        loop: AgenticLoop,
        call_usages: list[LLMCallUsage],
        request_started_at: float,
        zone_usage: dict[str, int] | None = None,
    ) -> FinalizedAssistantResponse:
        """Build the final filtered assistant response and aggregated stats."""
        output_result = filter_output(raw_final_text)
        filtered_structured_content = structured_content
        if structured_content is not None:
            # When suggestions are disabled for this turn the prompt already
            # omits the block, but models occasionally volunteer one anyway —
            # strip it here so the result is deterministic regardless of what
            # the model emitted.
            drop_suggestions = not self.prompt_builder.suggestions_enabled
            filtered_structured_content = replace(
                structured_content,
                answer=output_result.text,
                suggestions=[] if drop_suggestions else structured_content.suggestions,
            )
        return FinalizedAssistantResponse(
            loop_result=loop.complete(raw_final_text),
            response_text=output_result.text,
            output_filtered=bool(
                output_result.credentials_found or output_result.blocked
            ),
            stats=aggregate_call_usages(
                call_usages,
                fallback_model=self.model_name,
                total_wall_time_ms=(perf_counter() - request_started_at) * 1000,
                zone_usage=zone_usage,
                stage_phases=self._stage_timings.phases,
            ),
            structured_content=filtered_structured_content,
        )

    async def _build_gateway_response(
        self,
        *,
        raw_final_text: str,
        structured_content: StructuredAssistantContent | None,
        filtered_message: str,
        conversation_id: str,
        session_id: str,
        loop: AgenticLoop,
        input_filtered: bool,
        retrieved_memories: list[dict[str, str]],
        retrieved_messages: list[dict[str, str]] | None,
        call_usages: list[LLMCallUsage],
        request_started_at: float,
        user_message_id: str | None,
        zone_usage: dict[str, int] | None = None,
    ) -> AgentResponse:
        """Filter, persist, and wrap the final assistant answer."""
        finalized_response = self._finalize_assistant_response(
            raw_final_text=raw_final_text,
            structured_content=structured_content,
            loop=loop,
            call_usages=call_usages,
            request_started_at=request_started_at,
            zone_usage=zone_usage,
        )

        assistant_message_id = await self.persistence.persist_final_turn_messages(
            conversation_id=conversation_id,
            raw_assistant_text=raw_final_text,
            session_id=session_id,
            filtered_message=filtered_message,
            response_text=finalized_response.response_text,
            stats=finalized_response.stats,
            structured_content=finalized_response.structured_content,
            user_message_id=user_message_id,
            used_tools=self.used_tools,
        )

        return AgentResponse(
            structured_content=_require_structured_content(
                finalized_response.structured_content,
                finalized_response.response_text,
            ),
            loop_result=finalized_response.loop_result,
            input_filtered=input_filtered,
            output_filtered=finalized_response.output_filtered,
            retrieved_memories=retrieved_memories,
            retrieved_messages=retrieved_messages,
            stats=finalized_response.stats,
            assistant_message_id=assistant_message_id,
            used_tools=self.used_tools,
        )

    async def _build_prepared_turn_response(
        self,
        *,
        prepared_turn: TurnPreparation,
        raw_final_text: str,
        structured_content: StructuredAssistantContent | None,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
    ) -> AgentResponse:
        """Build the final gateway response from one prepared turn."""
        return await self._build_gateway_response(
            raw_final_text=raw_final_text,
            structured_content=structured_content,
            filtered_message=prepared_turn.filtered_message,
            conversation_id=conversation_id,
            session_id=session_id,
            loop=loop,
            input_filtered=prepared_turn.input_filtered,
            retrieved_memories=prepared_turn.prepared_context.retrieved_memories,
            retrieved_messages=prepared_turn.prepared_context.retrieved_messages,
            call_usages=prepared_turn.call_usages,
            request_started_at=prepared_turn.request_started_at,
            user_message_id=prepared_turn.user_message_id,
            zone_usage=prepared_turn.prepared_context.zone_usage,
        )

    async def _build_long_term_memory_answer_text(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
    ) -> str:
        """Build the answer text for a long-term memory question.

        With graph memory enabled the answer lists the user's valid
        graph facts; an empty graph (or any lookup failure) falls back
        to the legacy template answer.
        """
        if self.graph_facts_fn is not None:
            try:
                fact_lines = await self.graph_facts_fn()
            except Exception:
                logger.warning(
                    "Graph facts lookup failed; using template answer",
                    exc_info=True,
                )
                fact_lines = []
            if fact_lines:
                import re

                header = (
                    "Вот что я сейчас помню о тебе:"  # noqa: RUF001
                    if re.search(
                        r"[а-яё]",  # noqa: RUF001
                        prepared_turn.filtered_message,
                        re.IGNORECASE,
                    )
                    else "Here is what I currently remember about you:"
                )
                return "\n".join([header, *fact_lines])
        return LongTermMemoryResolver().build_response(
            prepared_turn.prepared_context.entries,
            session_id=session_id,
            question_text=prepared_turn.filtered_message,
        )

    async def _build_long_term_memory_response(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
    ) -> AgentResponse:
        """Build the final response for a long-term memory question."""
        raw_final_text = await self._build_long_term_memory_answer_text(
            prepared_turn=prepared_turn,
            session_id=session_id,
        )
        if prepared_turn.run_id:
            return await self._commit_structured_final(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                structured_content=StructuredAssistantContent(
                    answer=raw_final_text,
                ),
                output_filtered=False,
            )
        return await self._build_prepared_turn_response(
            prepared_turn=prepared_turn,
            raw_final_text=raw_final_text,
            structured_content=None,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
        )

    async def _complete_prepared_turn(
        self,
        *,
        prepared_turn: TurnPreparation,
        raw_final_text: str,
        structured_content: StructuredAssistantContent | None,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
    ) -> AgentResponse:
        """Persist and return the final response for one prepared turn."""
        if prepared_turn.run_id:
            finalized = self._finalize_assistant_response(
                raw_final_text=raw_final_text,
                structured_content=structured_content,
                loop=loop,
                call_usages=prepared_turn.call_usages,
                request_started_at=prepared_turn.request_started_at,
                zone_usage=prepared_turn.prepared_context.zone_usage,
            )
            content = _require_structured_content(
                finalized.structured_content,
                finalized.response_text,
            )
            return await self._commit_structured_final(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                structured_content=content,
                output_filtered=finalized.output_filtered,
            )
        return await self._build_prepared_turn_response(
            prepared_turn=prepared_turn,
            raw_final_text=raw_final_text,
            structured_content=structured_content,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
        )

    async def _prepare_turn_input(
        self,
        *,
        message: str,
        channel: str,
        injection_flag: bool,
        images: list[str] | None,
        loop: AgenticLoop,
    ) -> TurnInputState | AgentResponse:
        """Filter the incoming user text and audit uploaded images."""
        input_result = filter_input(message)
        input_filtered = (
            input_result.credentials_found or input_result.injection_flagged
        )
        uploaded_images = images or []

        if uploaded_images and self.broker.audit_fn is not None:
            import json as _json

            await self.broker.audit_fn(
                "image_upload",
                "chat",
                parameters=_json.dumps(
                    {"count": len(uploaded_images), "channel": channel}
                ),
            )

        self.broker.rate_limiter = ToolRateLimiter(injection_flag=injection_flag)
        if input_result.blocked:
            return AgentResponse(
                structured_content=StructuredAssistantContent(
                    answer="[Blocked: potential security threat detected]"
                ),
                loop_result=loop.complete(""),
                input_filtered=True,
                output_filtered=False,
            )

        return TurnInputState(
            filtered_message=input_result.text,
            input_filtered=input_filtered,
            uploaded_images=uploaded_images,
        )

    async def _build_turn_messages(
        self,
        *,
        session_id: str,
        conversation_id: str,
        filtered_message: str,
        uploaded_images: list[str],
        attached_files: list[str] | None,
        meta: dict | None,
        client_message_id: str | None = None,
    ) -> tuple[object, str | None, list[dict[str, object]]]:
        """Load memory context, save the user message, and build prompt messages."""
        recall_started_at = perf_counter()
        prepared_context = await self.memory_loader.load_prepared_context(
            session_id=session_id,
            filtered_message=filtered_message,
            conversation_id=conversation_id,
        )
        self._stage_timings.record(
            "Memory recall", (perf_counter() - recall_started_at) * 1000
        )
        user_content = build_multimodal_content(
            filtered_message,
            uploaded_images,
        )
        user_message_id = None
        if not self.prompt_builder.scheduled_task_mode:
            user_message_id = await self.persistence.save_user_message(
                conversation_id=conversation_id,
                user_content=user_content,
                attached_files=attached_files or [],
                uploaded_images=uploaded_images,
                meta=meta,
                client_message_id=client_message_id,
            )
        context_started_at = perf_counter()
        messages = self.prompt_builder.build_prompt_messages(
            context_entries=prepared_context.entries,
            memory_guidance=prepared_context.memory_guidance,
            log_context_fn=self._log_context_composition,
        )
        self._stage_timings.record(
            "Context", (perf_counter() - context_started_at) * 1000
        )
        self.prompt_builder.append_turn_reminder_and_user_message(
            messages=messages,
            user_content=user_content,
        )
        return prepared_context, user_message_id, messages

    async def _prepare_turn_state(
        self,
        *,
        message: str,
        channel: str,
        llm_call: object,
        session_id: str,
        injection_flag: bool,
        conversation_id: str,
        loop: AgenticLoop,
        images: list[str] | None,
        attached_files: list[str] | None,
        meta: dict | None,
        agent_run_id: str = "",
        client_message_id: str | None = None,
    ) -> TurnPreparation | AgentResponse:
        """Prepare one user turn before entering the model loop."""
        turn_input = await self._prepare_turn_input(
            message=message,
            channel=channel,
            injection_flag=injection_flag,
            images=images,
            loop=loop,
        )
        if isinstance(turn_input, AgentResponse):
            return turn_input

        request_started_at = perf_counter()
        call_usages: list[LLMCallUsage] = []
        prepared_context, user_message_id, messages = await self._build_turn_messages(
            session_id=session_id,
            conversation_id=conversation_id,
            filtered_message=turn_input.filtered_message,
            uploaded_images=turn_input.uploaded_images,
            attached_files=attached_files or [],
            meta=meta,
            client_message_id=client_message_id,
        )
        pending_input_resume = (
            meta.get("pending_input_resume") if isinstance(meta, dict) else None
        )
        if isinstance(pending_input_resume, dict):
            snapshot = pending_input_resume.get("transcript_snapshot")
            if not isinstance(snapshot, list):
                raise RuntimeError("pending-input resume has no transcript snapshot")
            messages = [dict(item) for item in snapshot if isinstance(item, dict)]
            messages.extend(
                [
                    {
                        "role": "system",
                        "content": (
                            "The user supplied the requested typed input. Treat it "
                            "as data, resume the same plan, and make a new native "
                            "decision. Requested input (JSON): "
                            + json.dumps(
                                {
                                    "question": pending_input_resume.get("question"),
                                    "expected_input_schema": pending_input_resume.get(
                                        "expected_input_schema"
                                    ),
                                    "answer": pending_input_resume.get("answer"),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                        ),
                    },
                    {"role": "user", "content": turn_input.filtered_message},
                ]
            )
        # Scheduled tasks drive tool use via their own declared allowlist
        # (enforced with retries by the scheduler), NOT via keyword-forced
        # prefetch and the current-data guard. Neutralise the keyword forcing so
        # the agent decides for itself — universal behaviour instead of a
        # hardcoded "current-data word -> mandatory web_search" special case.
        canonical_authority_turn = (
            await self.turn_store.get_agent_turn(agent_run_id)
            if agent_run_id and self.turn_store is not None
            else None
        )
        structured_turn = canonical_authority_turn is not None
        clarification_question: str | None = None
        if self.prompt_builder.scheduled_task_mode:
            requirements = requirements_with_expected_tools(
                ResponseRequirements(),
                tuple(self._forced_tools),
            )
            satisfaction = RequirementSatisfaction()
        else:
            # Every turn delegates semantic selection to the model. Only
            # explicit UI/caller requirements create backend obligations.
            requirements = ResponseRequirements()
            if self._forced_tools and get_policy().get_bool(
                "tools.manual_control_enabled"
            ):
                requirements = requirements_with_expected_tools(
                    requirements,
                    tuple(self._forced_tools),
                )
            satisfaction = RequirementSatisfaction()
        question_route = await self.memory_loader.prepare_turn_memory_context(
            llm_call=llm_call,
            session_id=session_id,
            conversation_id=conversation_id,
            filtered_message=turn_input.filtered_message,
            user_message_id=user_message_id,
            channel=channel,
            messages=messages,
            prepared_context=prepared_context,
            append_memory_context_fn=self.prompt_builder.append_memory_context_messages_async,
        )

        if structured_turn and isinstance(pending_input_resume, dict):
            obligations = tuple(
                ToolObligation(
                    run_id=agent_run_id,
                    obligation_id=str(item.get("obligation_id", "")),
                    required_tool=str(item.get("required_tool", "")),
                    source=str(item.get("source", "")),
                    evidence_key=str(item.get("evidence_key", "")),
                    argument_predicate=(
                        dict(item["argument_predicate"])
                        if isinstance(item.get("argument_predicate"), dict)
                        else None
                    ),
                    status=ObligationStatus(str(item.get("status", "open"))),
                )
                for item in (canonical_authority_turn or {}).get("obligations", [])
                if isinstance(item, dict)
            )
        else:
            obligations = (
                build_tool_obligations(
                    run_id=agent_run_id,
                    requirements=requirements,
                )
                if structured_turn
                else ()
            )
        required_names = {
            obligation.required_tool for obligation in obligations
        } or set(requirements.must_use_tools)
        openai_tools = self.prompt_builder.select_openai_tools(
            turn_input.filtered_message,
            attached_files=attached_files or [],
            required_names=required_names,
            required_only=False,
            semantic_selection=structured_turn,
        )
        if structured_turn:
            openai_tools = with_semantic_controls(openai_tools)
        if obligations:
            exposed_names = {
                str(item.get("function", {}).get("name", ""))
                for item in openai_tools or []
                if isinstance(item.get("function"), dict)
            }
            missing_schemas = sorted(required_names - exposed_names)
            if missing_schemas:
                raise RuntimeError(
                    "Required tool schemas are unavailable: "
                    f"{', '.join(missing_schemas)}"
                )
            await self.turn_store.upsert_agent_turn_obligations(
                agent_run_id,
                [
                    {
                        "obligation_id": item.obligation_id,
                        "required_tool": item.required_tool,
                        "source": item.source,
                        "evidence_key": item.evidence_key,
                        "argument_predicate": item.argument_predicate,
                    }
                    for item in obligations
                ],
            )
        canonical_turn = (
            await self.turn_store.get_agent_turn(agent_run_id)
            if structured_turn
            else None
        )
        return TurnPreparation(
            filtered_message=turn_input.filtered_message,
            input_filtered=turn_input.input_filtered,
            request_started_at=request_started_at,
            call_usages=call_usages,
            prepared_context=prepared_context,
            user_message_id=user_message_id,
            messages=messages,
            openai_tools=openai_tools,
            question_route=question_route,
            requirements=requirements,
            satisfaction=satisfaction,
            run_id=agent_run_id if structured_turn else None,
            finalization_id=(
                str(canonical_turn.get("finalization_id", ""))
                if canonical_turn is not None
                else None
            ),
            obligations=obligations,
            clarification_question=clarification_question,
        )

    async def process_message(
        self,
        message: str,
        channel: str,
        llm_call: object,
        session_id: str = "",
        injection_flag: bool = False,
        conversation_id: str = "",
        images: list[str] | None = None,
        attached_files: list[str] | None = None,
        meta: dict | None = None,
        agent_run_id: str = "",
        forced_tools: list[str] | None = None,
        client_message_id: str | None = None,
    ) -> AgentResponse:
        """Process a user message through the agentic loop.

        Args:
            message: User message text.
            channel: Originating channel name.
            llm_call: Async callable for LLM completion.
            session_id: Current session identifier.
            injection_flag: True if injection detected.
            conversation_id: Conversation grouping ID (required).
            images: Inline images attached to the current message.
            attached_files: File names attached to this message.
            meta: Extra metadata to store with the message.
            agent_run_id: Optional persisted run identifier for action prompts.
            forced_tools: Tool names the user selected to force for this turn.
            client_message_id: Optional client-stable id for the user message.

        Returns:
            AgentResponse with final text.

        Raises:
            ValueError: If conversation_id is empty.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        self._forced_tools = forced_tools or []
        if self._forced_tools:
            registered_tools = set(self.broker.registry.tool_names)
            unknown_tools = sorted(set(self._forced_tools) - registered_tools)
            if unknown_tools:
                raise ValueError(f"Unknown forced tool(s): {', '.join(unknown_tools)}")
        await self.concurrency.acquire()
        self._current_run_id = agent_run_id
        try:
            return await self._process_turn(
                message,
                channel,
                llm_call,
                session_id,
                injection_flag,
                conversation_id,
                images=images or [],
                attached_files=attached_files,
                meta=meta,
                agent_run_id=agent_run_id,
                client_message_id=client_message_id,
            )
        finally:
            self.concurrency.release()
            self._current_run_id = ""

    async def resume_structured_confirmation(
        self,
        *,
        confirmation_id: str,
        decision: str,
        llm_call: object,
        scope: str = "once",
        preliminary_call_usages: list[LLMCallUsage] | None = None,
    ) -> AgentResponse:
        """Decide and resume one core-authority confirmation continuation."""
        return await confirm_ops.resume_structured_confirmation(
            self,
            confirmation_id=confirmation_id,
            decision=decision,
            llm_call=llm_call,
            scope=scope,
            preliminary_call_usages=preliminary_call_usages,
        )

    def _record_provider_attempt(self, record: AttemptRecord) -> None:
        """Queue one durable provider-attempt event from a sync callback."""
        recorder = self.run_event_recorder
        if recorder is None:
            return
        payload: dict[str, object] = {"error": record.error or ""}
        if record.usage is not None:
            payload["usage"] = asdict(record.usage)
        task = asyncio.create_task(
            recorder.record_provider_attempt(
                phase=record.phase,
                attempt=record.attempt,
                payload=payload,
            )
        )
        self._provider_attempt_tasks.append(task)

    async def _flush_provider_attempts(self) -> None:
        """Wait until all authoritative provider-attempt events are stored."""
        tasks, self._provider_attempt_tasks = self._provider_attempt_tasks, []
        if tasks:
            await asyncio.gather(*tasks)

    async def _flush_provider_attempts_logged(self) -> None:
        """Flush provider-attempt events, logging failures instead of raising.

        A write failure here must not replace an outcome that is already
        decided (committed final, typed failure, or an exception in flight).
        """
        tasks, self._provider_attempt_tasks = self._provider_attempt_tasks, []
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.error(
                    "Failed to persist provider-attempt telemetry: %s",
                    result,
                )

    @staticmethod
    def _json_safe(value: object) -> object:
        """Normalize an arbitrary tool result into JSON-compatible data."""
        return confirm_ops._json_safe(value)

    def _restore_structured_preparation(
        self,
        canonical: dict[str, object],
        invocation_id: str,
    ) -> TurnPreparation:
        """Rebuild a structured turn from its canonical batch transcript."""
        return confirm_ops.restore_structured_preparation(
            self,
            canonical,
            invocation_id,
        )

    @staticmethod
    def _last_user_text(messages: list[dict[str, object]]) -> str:
        """Extract a conservative text hint from the latest user message."""
        return confirm_ops._last_user_text(messages)

    def _paused_response_from_canonical(
        self,
        canonical: dict[str, object],
        loop: AgenticLoop,
    ) -> AgentResponse:
        """Build PAUSED from canonical confirmation rows after a sibling decision."""
        return confirm_ops._paused_response_from_canonical(self, canonical, loop)

    @staticmethod
    def _agent_response_from_terminal_turn(
        canonical: dict[str, object],
    ) -> AgentResponse | None:
        """Return a replay response when a confirmation targets a terminal turn."""
        return confirm_ops._agent_response_from_terminal_turn(canonical)

    async def _process_turn(
        self,
        message: str,
        channel: str,
        llm_call: object,
        session_id: str,
        injection_flag: bool,
        conversation_id: str = "",
        images: list[str] | None = None,
        attached_files: list[str] | None = None,
        meta: dict | None = None,
        agent_run_id: str = "",
        client_message_id: str | None = None,
    ) -> AgentResponse:
        """Process one fully handled user turn through the agent loop.

        Args:
            message: User message text.
            channel: Originating channel.
            llm_call: Async callable for LLM.
            session_id: Session identifier.
            injection_flag: Injection flag state.
            conversation_id: Conversation grouping ID.
            images: Inline images attached to the current message.
            attached_files: File names attached to this message.
            meta: Extra metadata to store with the message.
            agent_run_id: Runtime agent run identifier for resumable actions.
            client_message_id: Optional client-stable id for the user message.

        Returns:
            AgentResponse with results.
        """
        active_started_at = perf_counter()
        active_core_turn_id = ""
        try:
            self._provider_attempt_tasks = []
            await self._load_skill_index()
            self.tool_executor.tool_call_count = 0
            self.tool_executor.tool_names_used = []
            self.tool_executor.executed_tools = []
            self._stage_timings = StageTimings()
            self.tool_executor.stage_timings = self._stage_timings
            canonical_authority_turn = (
                await self.turn_store.get_agent_turn(agent_run_id)
                if agent_run_id and self.turn_store is not None
                else None
            )
            if canonical_authority_turn is not None:
                active_core_turn_id = agent_run_id
            # Propagate turn context to tool handlers via the broker.
            self.tool_executor.current_context = {
                "conversation_id": conversation_id,
                "session_id": session_id,
                "channel": channel,
                "agent_run_id": agent_run_id,
                "tool_policy_version": str(
                    (canonical_authority_turn or {}).get(
                        "tool_policy_version",
                        str(
                            (meta or {}).get("tool_policy_version", TOOL_POLICY_VERSION)
                        ),
                    )
                ),
                "scheduled_task_id": str(
                    (self.scheduled_task_run or {}).get("task_id", "")
                ),
            }

            # Programmatic skill search before processing
            skill_context = await self._search_skills_for_message(message)
            if skill_context:
                self.prompt_builder.active_skill_context = skill_context

            self._notify_primary_started()
            response = await self._run_turn_with_primary_notification(
                message=message,
                channel=channel,
                llm_call=llm_call,
                session_id=session_id,
                injection_flag=injection_flag,
                conversation_id=conversation_id,
                images=images,
                attached_files=attached_files,
                meta=meta,
                agent_run_id=agent_run_id,
                client_message_id=client_message_id,
            )

            # Background skill generation after processing
            completion_tokens = (
                response.stats.completion_tokens if response.stats else 0
            )
            if (
                response.outcome is None
                or response.outcome.kind is TurnOutcomeKind.FINAL
            ):
                self._schedule_skill_generation(
                    message=message,
                    tool_call_count=self.tool_executor.tool_call_count,
                    completion_tokens=completion_tokens,
                    llm_call=llm_call,
                )

            # Log skill usage if skills were matched
            duration_ms = int(response.stats.timing.total_ms if response.stats else 0)
            await self._log_skill_usage(message, duration_ms)

            return response
        finally:
            await self._flush_provider_attempts_logged()
            if active_core_turn_id and self.turn_store is not None:
                try:
                    await self.turn_store.append_agent_turn_event(
                        turn_id=active_core_turn_id,
                        event_type="active_time_recorded",
                        phase="foreground",
                        payload={
                            "duration_ms": max(
                                0,
                                int((perf_counter() - active_started_at) * 1000),
                            )
                        },
                    )
                except Exception:
                    logger.exception("Failed to persist structured turn active time")
            self.prompt_builder.active_skill_context = None
            self._notify_primary_finished()

    def _notify_primary_started(self) -> None:
        """Notify the scheduler that the primary turn has started."""
        if self.notify_primary_started is not None:
            self.notify_primary_started()

    def _notify_primary_finished(self) -> None:
        """Notify the scheduler that the primary turn has finished."""
        if self.notify_primary_finished is not None:
            self.notify_primary_finished()

    async def _run_turn_with_primary_notification(
        self,
        *,
        message: str,
        channel: str,
        llm_call: object,
        session_id: str,
        injection_flag: bool,
        conversation_id: str,
        images: list[str] | None,
        attached_files: list[str] | None,
        meta: dict | None,
        agent_run_id: str = "",
        client_message_id: str | None = None,
    ) -> AgentResponse:
        """Run one turn after the primary-start notification is already sent."""
        loop = AgenticLoop(injection_flag=injection_flag)
        loop.start()
        prepared_turn = await self._prepare_turn_state(
            message=message,
            channel=channel,
            llm_call=llm_call,
            session_id=session_id,
            injection_flag=injection_flag,
            conversation_id=conversation_id,
            loop=loop,
            images=images,
            attached_files=attached_files,
            meta=meta,
            agent_run_id=agent_run_id,
            client_message_id=client_message_id,
        )
        if isinstance(prepared_turn, AgentResponse):
            canonical = (
                await self.turn_store.get_agent_turn(agent_run_id)
                if agent_run_id and self.turn_store is not None
                else None
            )
            if canonical is not None:
                minimal = TurnPreparation(
                    filtered_message=message,
                    input_filtered=True,
                    request_started_at=perf_counter(),
                    call_usages=[],
                    prepared_context=PreparedContext(
                        entries=[],
                        retrieved_memories=[],
                        retrieved_messages=None,
                        memory_guidance=None,
                    ),
                    user_message_id=None,
                    messages=[],
                    openai_tools=None,
                    question_route=route_memory_question(""),
                    requirements=ResponseRequirements(),
                    satisfaction=RequirementSatisfaction(),
                    run_id=agent_run_id,
                    finalization_id=str(canonical.get("finalization_id", "")),
                )
                return await self._commit_structured_failure(
                    prepared_turn=minimal,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    loop=loop,
                    code="input_blocked",
                    message="The request was blocked by the input safety policy.",
                )
            return prepared_turn
        return await self._run_prepared_turn(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            llm_call=llm_call,
            channel=channel,
        )

    async def _run_prepared_turn(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
        llm_call: object,
        channel: str,
    ) -> AgentResponse:
        """Run one already prepared turn either through memory-only or model path."""
        if prepared_turn.clarification_question:
            return await self._commit_structured_final(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                structured_content=StructuredAssistantContent(
                    answer=prepared_turn.clarification_question,
                    suggestions=[],
                ),
                output_filtered=False,
            )
        if prepared_turn.question_route.long_term_memory_question:
            return await self._build_long_term_memory_response(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
            )

        if prepared_turn.run_id and (
            prepared_turn.obligations or prepared_turn.openai_tools
        ):
            return await self._run_structured_authority_turn(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                llm_call=llm_call,
                channel=channel,
            )

        # Force the first exact tool. The final gate selects each remaining
        # required tool after successful execution.
        forced = self._forced_tools
        forced_tool_choice: dict[str, object] | str | None = None
        if forced and get_policy().get_bool("tools.manual_control_enabled"):
            forced_tool_choice = {
                "type": "function",
                "function": {"name": forced[0]},
            }
        model_loop_result = await self.llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=prepared_turn.messages,
            openai_tools=prepared_turn.openai_tools,
            call_usages=prepared_turn.call_usages,
            channel=channel,
            tool_executor=self.tool_executor,
            forced_tool_choice=forced_tool_choice,
            requirements=prepared_turn.requirements,
            satisfaction=prepared_turn.satisfaction,
            suggestions_enabled=self.prompt_builder.suggestions_enabled,
            thinking_enabled=(
                False
                if prepared_turn.run_id and not prepared_turn.openai_tools
                else None
            ),
            response_format=(
                LlmResponseFormat.TEXT
                if prepared_turn.run_id and not prepared_turn.openai_tools
                else None
            ),
        )

        return await self._complete_prepared_turn(
            prepared_turn=prepared_turn,
            raw_final_text=model_loop_result.raw_final_text,
            structured_content=model_loop_result.structured_content,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
        )

    async def _run_structured_authority_turn(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
        llm_call: object,
        channel: str,
    ) -> AgentResponse:
        """Run native action rounds, evidence gate, synthesis, and core commit."""
        return await confirm_ops.run_structured_authority_turn(
            self,
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            llm_call=llm_call,
            channel=channel,
        )

    async def _require_structured_capabilities(
        self,
        *,
        stage: str,
        exact_choice: bool = False,
    ) -> object:
        """Fail closed unless the active provider declares the required phase."""
        return await confirm_ops.require_structured_capabilities(
            self,
            stage=stage,
            exact_choice=exact_choice,
        )

    async def _pause_for_missing_input(
        self,
        *,
        prepared_turn: TurnPreparation,
        loop: AgenticLoop,
        llm_call: object,
        session_id: str,
        conversation_id: str,
        question: str,
        expected_input_schema: dict[str, object],
        assistant_message: dict[str, object],
    ) -> AgentResponse:
        """Persist one typed missing-input pause and return no final answer."""
        return await confirm_ops.pause_for_missing_input(
            self,
            prepared_turn=prepared_turn,
            loop=loop,
            llm_call=llm_call,
            session_id=session_id,
            conversation_id=conversation_id,
            question=question,
            expected_input_schema=expected_input_schema,
            assistant_message=assistant_message,
        )

    def _build_paused_structured_response(
        self,
        *,
        prepared_turn: TurnPreparation,
        loop: AgenticLoop,
    ) -> AgentResponse:
        """Return PAUSED without an assistant or dialog record."""
        return confirm_ops._build_paused_structured_response(
            self,
            prepared_turn=prepared_turn,
            loop=loop,
        )

    async def _commit_structured_failure(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
        code: str,
        message: str,
    ) -> AgentResponse:
        """Filter and atomically commit one interactive typed failure."""
        return await confirm_ops.commit_structured_failure(
            self,
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code=code,
            message=message,
        )

    async def _commit_structured_final(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
        structured_content: StructuredAssistantContent,
        output_filtered: bool,
    ) -> AgentResponse:
        """Atomically commit one accepted structured final."""
        return await confirm_ops.commit_structured_final(
            self,
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            structured_content=structured_content,
            output_filtered=output_filtered,
        )

    async def _commit_core_interactive_outcome(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
        conversation_id: str,
        loop: AgenticLoop,
        kind: TurnOutcomeKind,
        structured_content: StructuredAssistantContent,
        output_filtered: bool,
        error: TerminalError | None,
    ) -> AgentResponse:
        """Commit message, dialog queue, turn, and canonical outcome together."""
        return await confirm_ops._commit_core_interactive_outcome(
            self,
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            kind=kind,
            structured_content=structured_content,
            output_filtered=output_filtered,
            error=error,
        )

    @property
    def pending_red_calls(self) -> list[PendingAction]:
        """Return pending RED zone actions."""
        return self.tool_executor.pending_red_calls

    @property
    def used_tools(self) -> list[dict]:
        """Return executed tools for the current turn."""
        return self.tool_executor.executed_tools

    def clear_pending(self) -> None:
        """Clear pending RED zone calls."""
        self.tool_executor.clear_pending()

    def consume_pending_action(self, action_id: str) -> None:
        """Remove one decided RED action from the active pending ledger."""
        self.tool_executor.consume_pending(action_id)
