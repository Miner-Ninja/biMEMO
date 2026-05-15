"""Gateway orchestrator for the agentic loop.

Ties together LLM calls, native tool calling, zone-based
execution, and result feedback into a single loop.
Uses asyncio.gather for parallel tool execution.

Both sync and async context_fn/save_fn are supported so the
gateway can call the orchestrator helpers without caring about
their storage transport details.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING

from backend.agent.agent_loop_models import (
    AgentResponse,
    FinalizedAssistantResponse,
    TurnInputState,
    TurnPreparation,
)
from backend.agent.finalization import detect_turn_requirements
from backend.agent.llm_caller import (
    FORCED_WEB_SEARCH_CLASSIFIER_PROMPT,
    LLMCaller,
)
from backend.agent.loop import (
    AgenticLoop,
)
from backend.agent.memory_context import MemoryContextLoader
from backend.agent.message_persistence import MessagePersistence
from backend.agent.prompt_builder import PromptBuilder
from backend.agent.tool_executor import ToolExecutor
from backend.ipc.models import build_multimodal_content
from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.structured_response import (
    SKILL_EXTRACTION_SCHEMA,
    StructuredAssistantContent,
)
from backend.llm.usage import aggregate_call_usages
from backend.memory.classification.resolver import (
    LongTermMemoryResolver,
)
from backend.permissions import ActionDecisionService, ActionGrantReader
from backend.security.content_filter import (
    filter_input,
    filter_output,
)
from backend.tools.rate_limits import ToolRateLimiter
from backend.trust.confirmation import (
    ConfirmationManager,
)
from backend.trust.zones import extract_user_intent_tools


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.concurrency import AgentConcurrency
    from backend.agent.tool_executor import PendingActionRecorder
    from backend.llm.usage import LLMCallUsage
    from backend.tools.broker import ToolBroker
    from backend.trust.confirmation import PendingAction


logger = logging.getLogger(__name__)

FRESH_SKILL_WINDOW_HOURS = 24
BACKGROUND_SKILL_TASK_NAME = "background-skill-generation"
SKILL_JSON_RESPONSE_DESCRIPTION = (
    '  "description": "One sentence: when and why to use this skill",\n'
)
SKILL_JSON_RESPONSE_TRIGGER = (
    '  "trigger": "Description of what user request triggers this skill",\n'
)
SKILL_JSON_RESPONSE_PROCEDURE_STEP = (
    '    {"tool": "web_search", "query": '
    '"search query text", "note": "optional note"},\n'
)
SKILL_JSON_RESPONSE_ERROR = (
    '    {"name": "Error name", "fix": "How to handle this error"}\n'
)
SKILL_JSON_RESPONSE_VERIFICATION = (
    '  "verification": "How to confirm the result is correct"\n'
)

# Re-export for backward compatibility (used by tests)
__all__ = [
    "FORCED_WEB_SEARCH_CLASSIFIER_PROMPT",
    "AgentOrchestrator",
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

    Attributes:
        broker: Tool broker for execution.
        concurrency: Cross-channel concurrency control.
        context_fn: Callable returning context entries.
    """

    broker: ToolBroker
    concurrency: AgentConcurrency
    context_fn: Callable
    evidence_bundle_fn: Callable | None = None
    save_fn: Callable | None = None
    save_record_fn: Callable | None = None
    write_memory_route_diagnostic_fn: Callable | None = None
    notify_primary_started: Callable[[], None] | None = None
    notify_primary_finished: Callable[[], None] | None = None
    confirmation_mgr: ConfirmationManager = field(default_factory=ConfirmationManager)
    web_search_classifier_fn: Callable | None = None
    memory_recall_classifier_fn: Callable | None = None
    sse_queue: object | None = None
    user_profile: dict[str, str] = field(default_factory=dict)
    model_name: str = ""
    list_skills_fn: Callable | None = None
    search_skills_fn: Callable | None = None
    create_skill_fn: Callable | None = None
    log_skill_usage_fn: Callable | None = None
    action_grants: ActionGrantReader | None = None
    pending_action_recorder: PendingActionRecorder | None = None

    def __post_init__(self) -> None:
        """Initialize delegate objects after dataclass construction."""
        self.tool_executor = ToolExecutor(
            self.broker,
            self.confirmation_mgr,
            action_decisions=ActionDecisionService(self.action_grants),
            pending_action_recorder=self.pending_action_recorder,
        )
        self.llm_caller = LLMCaller(
            self.model_name,
            self.web_search_classifier_fn,
            self.memory_recall_classifier_fn,
            self.broker,
        )
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
        self.tool_executor.sse_queue = self.sse_queue
        self.llm_caller.sse_queue = self.sse_queue

    def __setattr__(self, name: str, value: object) -> None:
        """Sync delegate state when mutable attributes change post-init."""
        object.__setattr__(self, name, value)
        if name == "sse_queue":
            if hasattr(self, "tool_executor"):
                self.tool_executor.sse_queue = value
            if hasattr(self, "llm_caller"):
                self.llm_caller.sse_queue = value
        elif name == "save_fn" and hasattr(self, "persistence"):
            self.persistence.save_fn = value
        elif name == "save_record_fn" and hasattr(self, "persistence"):
            self.persistence.save_record_fn = value
        elif name == "context_fn" and hasattr(self, "memory_loader"):
            self.memory_loader.context_fn = value
        elif name == "web_search_classifier_fn" and hasattr(self, "llm_caller"):
            self.llm_caller.web_search_classifier_fn = value
        elif name == "memory_recall_classifier_fn" and hasattr(self, "llm_caller"):
            self.llm_caller.memory_recall_classifier_fn = value
        elif name == "model_name":
            if hasattr(self, "llm_caller"):
                self.llm_caller.model_name = value
            if hasattr(self, "prompt_builder"):
                self.prompt_builder.model_name = value

    async def _load_skill_index(self) -> None:
        """Load skill index into prompt builder if callback is available."""
        if self.list_skills_fn is None:
            return
        try:
            result = self.list_skills_fn
            if callable(result):
                result = result()
            if inspect.isawaitable(result):
                result = await result
            self.prompt_builder.skill_index = result or []
        except Exception:
            logger.warning("Failed to load skill index", exc_info=True)
            self.prompt_builder.skill_index = []

    async def _search_skills_for_message(self, message: str) -> str | None:
        """Search for a relevant skill before processing a message."""
        if self.search_skills_fn is None:
            return None
        try:
            logger.info("Searching skills for: %s", message[:80])
            result = self.search_skills_fn(message)
            if inspect.isawaitable(result):
                result = await result
            skills = result or []
            if not skills:
                logger.info("No matching skills found")
                self._matched_skill_ids = []
                return None
            # Store matched skill IDs for usage logging
            self._matched_skill_ids = [s.get("id", "") for s in skills if s.get("id")]
            # Return only hints (name + description), not full content
            hints = []
            for skill in skills:
                name = skill.get("name", "")
                desc = skill.get("description", "")
                hints.append(f"- {name}: {desc}")
            logger.info("Found %d relevant skills for message", len(skills))
            return "\n".join(hints)
        except Exception:
            logger.warning("Failed to search skills", exc_info=True)
            self._matched_skill_ids = []
            return None

    async def _log_skill_usage(
        self,
        message: str,
        duration_ms: int,
    ) -> None:
        """Log usage for matched skills."""
        if self.log_skill_usage_fn is None:
            return
        skill_ids = getattr(self, "_matched_skill_ids", [])
        if not skill_ids:
            return
        for skill_id in skill_ids:
            try:
                result = self.log_skill_usage_fn(
                    skill_id=skill_id,
                    question=message[:200],
                    status="success",
                    duration_ms=duration_ms,
                )
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.debug("Failed to log skill usage", exc_info=True)

    def _build_skill_name(self, message: str) -> str:
        """Build a kebab-case skill name from a user message."""
        name = message[:40].strip().lower()
        name = "-".join(name.split()[:5])
        name = "".join(c for c in name if c.isalnum() or c == "-")
        return name.strip("-") or "auto-skill"

    MIN_COMPLETION_TOKENS_FOR_SKILL = 300

    def _schedule_skill_generation(
        self,
        message: str,
        tool_call_count: int,
        completion_tokens: int,
        llm_call: object,
    ) -> None:
        """Schedule background skill generation if task was non-trivial."""
        if self.create_skill_fn is None:
            return
        logger.info(
            "Skill generation check: %d tool calls, %d completion tokens",
            tool_call_count,
            completion_tokens,
        )
        if tool_call_count < 1:
            return
        if completion_tokens < self.MIN_COMPLETION_TOKENS_FOR_SKILL:
            return
        background_task = asyncio.create_task(
            self._generate_and_save_skill(
                message=message,
                tool_call_count=tool_call_count,
                llm_call=llm_call,
            ),
            name=BACKGROUND_SKILL_TASK_NAME,
        )
        background_task.add_done_callback(self._log_background_skill_failure)

    def _log_background_skill_failure(self, task: asyncio.Task[None]) -> None:
        """Log one background skill-generation failure."""
        try:
            task.result()
        except Exception:
            logger.warning(
                "Background skill generation failed",
                exc_info=True,
            )

    async def _generate_and_save_skill(
        self,
        message: str,
        tool_call_count: int,
        llm_call: object,
    ) -> None:
        """Generate skill content via LLM and save to database."""
        try:
            tools_used = list(dict.fromkeys(self.tool_executor.tool_names_used))
            tool_seq = ", ".join(self.tool_executor.tool_names_used)
            name = self._build_skill_name(message)

            # Check for existing skill — skip if fresh (< 24h)
            if self.search_skills_fn is not None:
                try:
                    existing = self.search_skills_fn(message)
                    if inspect.isawaitable(existing):
                        existing = await existing
                    if existing:
                        for skill in existing:
                            updated = skill.get("updated_at", "")
                            if updated:
                                try:
                                    skill_time = datetime.fromisoformat(updated)
                                    age = datetime.now(tz=UTC) - skill_time
                                    if age < timedelta(hours=FRESH_SKILL_WINDOW_HOURS):
                                        logger.info(
                                            (
                                                "Skipping skill creation — "
                                                "similar skill '%s' is fresh "
                                                "(%s old)"
                                            ),
                                            skill.get("name", ""),
                                            age,
                                        )
                                        return
                                except ValueError, TypeError:
                                    pass
                        # Existing skill is old — will be updated via upsert
                        logger.info("Updating existing skill (older than 24h)")
                except Exception:
                    logger.debug("Duplicate check failed, proceeding", exc_info=True)

            skill_prompt = [
                {
                    "role": "system",
                    "content": "".join(
                        [
                            "You are a skill extractor. Based on the task below, ",
                            "create a reusable skill document.\n\n",
                            "Respond with a JSON object containing exactly 6 fields:\n",
                            "{\n",
                            '  "title": "Short skill name (5-10 words)",\n',
                            SKILL_JSON_RESPONSE_DESCRIPTION,
                            SKILL_JSON_RESPONSE_TRIGGER,
                            '  "procedure": [\n',
                            SKILL_JSON_RESPONSE_PROCEDURE_STEP,
                            '    {"tool": "web_search", "query": "another query"}\n',
                            "  ],\n",
                            '  "errors": [\n',
                            SKILL_JSON_RESPONSE_ERROR,
                            "  ],\n",
                            SKILL_JSON_RESPONSE_VERIFICATION,
                            "}\n\n",
                            "The skill must describe the PROCEDURE ",
                            "(what tools to call and in what order), ",
                            "NOT the data or results.\n",
                            "Write concisely. ",
                            "Write in the same language as the user's message.\n\n",
                            "IMPORTANT: Respond with JSON only. ",
                            "Do NOT call any tools or functions.",
                        ]
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Create a skill from this task:\n"
                        f"User request: {message}\n"
                        f"Tools used: {', '.join(tools_used)}\n"
                        f"Total tool calls: {tool_call_count}\n"
                    ),
                },
            ]

            # Call LLM with the skill-extraction schema so the provider
            # enforces the six required fields. Tools are disabled for this
            # call — small models should not be able to side-step the schema
            # by invoking a tool.
            llm_response = await llm_call(
                skill_prompt,
                options=LLMCallOptions(
                    response_format=LlmResponseFormat.JSON_SCHEMA,
                    response_schema=SKILL_EXTRACTION_SCHEMA,
                    tools=[],
                ),
            )
            raw_text = getattr(llm_response, "text", "") or ""

            if not raw_text or len(raw_text) < 20:
                logger.warning(
                    "Skill generation returned empty response. "
                    "Response type=%s, text=%r",
                    type(llm_response).__name__,
                    raw_text,
                )
                return

            # Parse schema-conformant JSON response from LLM
            skill_title = message[:80]
            skill_description = f"Procedure: {' → '.join(tools_used)}"
            skill_trigger = ""
            skill_procedure = "[]"
            skill_errors = "[]"
            skill_verification = ""

            try:
                parsed = json.loads(raw_text)
                skill_title = parsed.get("title", skill_title)
                skill_description = parsed.get("description", skill_description)
                skill_trigger = parsed.get("trigger", "")
                proc = parsed.get("procedure", [])
                skill_procedure = (
                    json.dumps(proc) if isinstance(proc, list) else str(proc)
                )
                errs = parsed.get("errors", [])
                skill_errors = json.dumps(errs) if isinstance(errs, list) else str(errs)
                skill_verification = parsed.get("verification", "")
            except json.JSONDecodeError:
                # Defensive fallback: a non-conforming provider may return
                # free-form text despite the strict schema. Preserve the
                # response as trigger_text rather than losing the skill.
                logger.warning(
                    "Skill LLM response is not JSON despite schema — "
                    "storing raw text as trigger",
                )
                skill_trigger = raw_text

            result = self.create_skill_fn(
                name=name,
                title=skill_title,
                description=skill_description,
                trigger=skill_trigger,
                procedure=skill_procedure,
                errors=skill_errors,
                verification=skill_verification,
                tool_sequence=tool_seq,
            )
            if inspect.isawaitable(result):
                await result
            logger.info(
                "Generated and saved skill '%s' after %d tool calls",
                name,
                tool_call_count,
            )
        except Exception:
            logger.warning("Failed to generate skill", exc_info=True)

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
            filtered_structured_content = replace(
                structured_content,
                answer=output_result.text,
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

        await self.persistence.persist_final_turn_messages(
            conversation_id=conversation_id,
            raw_assistant_text=raw_final_text,
            session_id=session_id,
            filtered_message=filtered_message,
            response_text=finalized_response.response_text,
            stats=finalized_response.stats,
            structured_content=finalized_response.structured_content,
            user_message_id=user_message_id,
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

    def _build_long_term_memory_answer_text(
        self,
        *,
        prepared_turn: TurnPreparation,
        session_id: str,
    ) -> str:
        """Build the answer text for a long-term memory question."""
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
        raw_final_text = self._build_long_term_memory_answer_text(
            prepared_turn=prepared_turn,
            session_id=session_id,
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
    ) -> tuple[object, str | None, list[dict[str, object]]]:
        """Load memory context, save the user message, and build prompt messages."""
        prepared_context = await self.memory_loader.load_prepared_context(
            session_id=session_id,
            filtered_message=filtered_message,
            conversation_id=conversation_id,
        )
        user_content = build_multimodal_content(
            filtered_message,
            uploaded_images,
        )
        user_message_id = await self.persistence.save_user_message(
            conversation_id=conversation_id,
            user_content=user_content,
            attached_files=attached_files or [],
            uploaded_images=uploaded_images,
            meta=meta,
        )
        messages = self.prompt_builder.build_prompt_messages(
            context_entries=prepared_context.entries,
            memory_guidance=prepared_context.memory_guidance,
            log_context_fn=self._log_context_composition,
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
        )
        requirements = detect_turn_requirements(turn_input.filtered_message)
        satisfaction = await self.llm_caller.append_required_tool_context(
            filtered_message=turn_input.filtered_message,
            messages=messages,
            requirements=requirements,
        )
        if "web_search" not in requirements.required_tools:
            web_search_satisfied = (
                await self.llm_caller.append_forced_web_search_context(
                    filtered_message=turn_input.filtered_message,
                    messages=messages,
                    llm_call=llm_call,
                    call_usages=call_usages,
                )
            )
            if web_search_satisfied:
                satisfaction = satisfaction.with_success("web_search")
        forced_recall_tokens = 0
        if "memory_recall" not in requirements.required_tools:
            forced_recall_tokens = (
                await self.llm_caller.append_forced_memory_recall_context(
                    filtered_message=turn_input.filtered_message,
                    messages=messages,
                )
            )
        if forced_recall_tokens > 0 and prepared_context.zone_usage is not None:
            prepared_context.zone_usage["recall_results"] = (
                prepared_context.zone_usage.get("recall_results", 0)
                + forced_recall_tokens
            )
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

        return TurnPreparation(
            filtered_message=turn_input.filtered_message,
            input_filtered=turn_input.input_filtered,
            request_started_at=request_started_at,
            call_usages=call_usages,
            prepared_context=prepared_context,
            user_message_id=user_message_id,
            messages=messages,
            openai_tools=self.prompt_builder.select_openai_tools(
                turn_input.filtered_message,
                attached_files=attached_files or [],
            ),
            question_route=question_route,
            requirements=requirements,
            satisfaction=satisfaction,
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

        Returns:
            AgentResponse with final text.

        Raises:
            ValueError: If conversation_id is empty.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        await self.concurrency.acquire()
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
            )
        finally:
            self.concurrency.release()

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

        Returns:
            AgentResponse with results.
        """
        try:
            await self._load_skill_index()
            self.tool_executor.tool_call_count = 0
            self.tool_executor.tool_names_used = []
            # Propagate turn context to tool handlers via the broker.
            self.tool_executor.current_context = {
                "conversation_id": conversation_id,
                "session_id": session_id,
                "channel": channel,
                "agent_run_id": agent_run_id,
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
            )

            # Background skill generation after processing
            completion_tokens = (
                response.stats.completion_tokens if response.stats else 0
            )
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
        )
        if isinstance(prepared_turn, AgentResponse):
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
        if prepared_turn.question_route.long_term_memory_question:
            return await self._build_long_term_memory_response(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
            )

        user_intent = extract_user_intent_tools(
            prepared_turn.filtered_message,
        )
        model_loop_result = await self.llm_caller.run_model_loop(
            loop=loop,
            llm_call=llm_call,
            messages=prepared_turn.messages,
            openai_tools=prepared_turn.openai_tools,
            call_usages=prepared_turn.call_usages,
            channel=channel,
            tool_executor=self.tool_executor,
            user_intent_tools=user_intent or None,
            requirements=prepared_turn.requirements,
            satisfaction=prepared_turn.satisfaction,
        )

        return await self._complete_prepared_turn(
            prepared_turn=prepared_turn,
            raw_final_text=model_loop_result.raw_final_text,
            structured_content=model_loop_result.structured_content,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
        )

    @property
    def pending_red_calls(self) -> list[PendingAction]:
        """Return pending RED zone actions."""
        return self.tool_executor.pending_red_calls

    def clear_pending(self) -> None:
        """Clear pending RED zone calls."""
        self.tool_executor.clear_pending()
