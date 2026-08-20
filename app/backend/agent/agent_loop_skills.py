"""Skill subsystem operations for the gateway agent loop.

Provides module-level functions for the skill index, search,
usage logging, background generation, and the quality appraisal
gate. Each function receives an AgentOrchestrator instance as
its first parameter where orchestrator state is needed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.structured_response import (
    SKILL_EXTRACTION_SCHEMA,
    SKILL_QUALITY_SCHEMA,
)


if TYPE_CHECKING:
    from backend.agent.agent_loop import AgentOrchestrator


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


# --- Skill quality appraisal thresholds ---
SKILL_QUALITY_AXIS_MAX = 25
SKILL_QUALITY_MIN_TOTAL = 68
SKILL_QUALITY_MIN_PER_AXIS = 8


@dataclass(frozen=True)
class SkillQualityVerdict:
    """Result of a skill-quality appraisal.

    Attributes:
        total: Sum of all four axis scores (0..100).
        accepted: True when total and per-axis floors are met.
        weakest_axis: Name of the axis with the lowest score.
    """

    total: int
    accepted: bool
    weakest_axis: str


def _appraise_skill_quality(appraisal: dict) -> SkillQualityVerdict:
    """Decide whether a skill draft passes the quality gate.

    This is a pure helper — it performs no I/O.

    Args:
        appraisal: Dict with keys matching SKILL_QUALITY_SCHEMA.

    Returns:
        SkillQualityVerdict with total, accepted, and weakest_axis.
    """
    axes = (
        ("reusability", appraisal.get("reusability", 0)),
        ("step_clarity", appraisal.get("step_clarity", 0)),
        ("future_applicability", appraisal.get("future_applicability", 0)),
        ("procedure_soundness", appraisal.get("procedure_soundness", 0)),
    )
    total = sum(score for _, score in axes)
    all_above_floor = all(score >= SKILL_QUALITY_MIN_PER_AXIS for _, score in axes)
    accepted = total >= SKILL_QUALITY_MIN_TOTAL and all_above_floor
    weakest_axis = min(axes, key=lambda item: item[1])[0]
    return SkillQualityVerdict(
        total=total,
        accepted=accepted,
        weakest_axis=weakest_axis,
    )


async def load_skill_index(orchestrator: AgentOrchestrator) -> None:
    """Load skill index into prompt builder if callback is available."""
    if orchestrator.list_skills_fn is None:
        return
    try:
        result = orchestrator.list_skills_fn
        if callable(result):
            result = result()
        if inspect.isawaitable(result):
            result = await result
        orchestrator.prompt_builder.skill_index = result or []
    except Exception:
        logger.warning("Failed to load skill index", exc_info=True)
        orchestrator.prompt_builder.skill_index = []


async def search_skills_for_message(
    orchestrator: AgentOrchestrator, message: str
) -> str | None:
    """Search for a relevant skill before processing a message."""
    if orchestrator.search_skills_fn is None:
        return None
    try:
        logger.info("Searching skills for: %s", message[:80])
        result = orchestrator.search_skills_fn(message)
        if inspect.isawaitable(result):
            result = await result
        skills = result or []
        if not skills:
            logger.info("No matching skills found")
            orchestrator._matched_skill_ids = []
            return None
        # Store matched skill IDs for usage logging
        orchestrator._matched_skill_ids = [
            s.get("id", "") for s in skills if s.get("id")
        ]
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
        orchestrator._matched_skill_ids = []
        return None


async def log_skill_usage(
    orchestrator: AgentOrchestrator,
    message: str,
    duration_ms: int,
) -> None:
    """Log usage for matched skills."""
    if orchestrator.log_skill_usage_fn is None:
        return
    skill_ids = getattr(orchestrator, "_matched_skill_ids", [])
    if not skill_ids:
        return
    for skill_id in skill_ids:
        try:
            result = orchestrator.log_skill_usage_fn(
                skill_id=skill_id,
                question=message[:200],
                status="success",
                duration_ms=duration_ms,
            )
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Failed to log skill usage", exc_info=True)


def _build_skill_name(orchestrator: AgentOrchestrator, message: str) -> str:
    """Build a kebab-case skill name from a user message."""
    name = message[:40].strip().lower()
    name = "-".join(name.split()[:5])
    name = "".join(c for c in name if c.isalnum() or c == "-")
    return name.strip("-") or "auto-skill"


async def run_skill_quality_appraisal(
    orchestrator: AgentOrchestrator,
    *,
    title: str,
    description: str,
    trigger: str,
    procedure: str,
    errors: str,
    verification: str,
    llm_call: object,
) -> SkillQualityVerdict | None:
    """Run a separate LLM call to appraise skill quality.

    Returns a SkillQualityVerdict on success, or None on any failure
    (fail-open: the caller should persist the draft anyway).
    """
    skill_text = (
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Trigger: {trigger}\n"
        f"Procedure: {procedure}\n"
        f"Errors: {errors}\n"
        f"Verification: {verification}"
    )
    appraiser_prompt = [
        {
            "role": "system",
            "content": (
                "You are a skill quality appraiser. Score the following "
                "skill draft on four axes (0-25 each):\n"
                "  - reusability: repeatable procedure vs one-off answer\n"
                "  - step_clarity: concrete tool/query steps vs vague prose\n"
                "  - future_applicability: likely to help future tasks\n"
                "  - procedure_soundness: steps plausibly achieve the stated task\n"
                "Score regardless of the skill's language. "
                "Respond with JSON only, do not call tools."
            ),
        },
        {
            "role": "user",
            "content": f"Appraise this skill:\n\n{skill_text}",
        },
    ]
    try:
        response = await llm_call(
            appraiser_prompt,
            options=LLMCallOptions(
                response_format=LlmResponseFormat.JSON_SCHEMA,
                response_schema=SKILL_QUALITY_SCHEMA,
                tools=[],
            ),
        )
        raw = getattr(response, "text", "") or ""
        if not raw:
            return None
        appraisal = json.loads(raw)
        # Validate required fields exist and are integers
        for axis in (
            "reusability",
            "step_clarity",
            "future_applicability",
            "procedure_soundness",
        ):
            if not isinstance(appraisal.get(axis), int):
                return None
        return _appraise_skill_quality(appraisal)
    except Exception:
        logger.warning(
            "Skill quality appraiser did not return a usable verdict",
            exc_info=True,
        )
        return None


def schedule_skill_generation(
    orchestrator: AgentOrchestrator,
    message: str,
    tool_call_count: int,
    completion_tokens: int,
    llm_call: object,
) -> None:
    """Schedule background skill generation if task was non-trivial."""
    if orchestrator.create_skill_fn is None:
        return
    logger.info(
        "Skill generation check: %d tool calls, %d completion tokens",
        tool_call_count,
        completion_tokens,
    )
    if tool_call_count < 1:
        return
    if completion_tokens < orchestrator.MIN_COMPLETION_TOKENS_FOR_SKILL:
        return
    background_task = asyncio.create_task(
        orchestrator._generate_and_save_skill(
            message=message,
            tool_call_count=tool_call_count,
            llm_call=llm_call,
        ),
        name=BACKGROUND_SKILL_TASK_NAME,
    )
    background_task.add_done_callback(orchestrator._log_background_skill_failure)


def log_background_skill_failure(
    orchestrator: AgentOrchestrator, task: asyncio.Task[None]
) -> None:
    """Log one background skill-generation failure."""
    try:
        task.result()
    except Exception:
        logger.warning(
            "Background skill generation failed",
            exc_info=True,
        )


async def generate_and_save_skill(
    orchestrator: AgentOrchestrator,
    message: str,
    tool_call_count: int,
    llm_call: object,
) -> None:
    """Generate skill content via LLM and save to database."""
    try:
        tools_used = list(dict.fromkeys(orchestrator.tool_executor.tool_names_used))
        tool_seq = ", ".join(orchestrator.tool_executor.tool_names_used)
        name = orchestrator._build_skill_name(message)

        # Check for existing skill — skip if fresh (< 24h)
        if orchestrator.search_skills_fn is not None:
            try:
                existing = orchestrator.search_skills_fn(message)
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
                "Skill generation returned empty response. Response type=%s, text=%r",
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

        parse_ok = True
        try:
            parsed = json.loads(raw_text)
            skill_title = parsed.get("title", skill_title)
            skill_description = parsed.get("description", skill_description)
            skill_trigger = parsed.get("trigger", "")
            proc = parsed.get("procedure", [])
            skill_procedure = json.dumps(proc) if isinstance(proc, list) else str(proc)
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
            parse_ok = False

        # Quality appraisal gate — only for conforming drafts
        if parse_ok:
            appraisal_verdict = await orchestrator._run_skill_quality_appraisal(
                title=skill_title,
                description=skill_description,
                trigger=skill_trigger,
                procedure=skill_procedure,
                errors=skill_errors,
                verification=skill_verification,
                llm_call=llm_call,
            )
            if appraisal_verdict is not None and not appraisal_verdict.accepted:
                logger.info(
                    "Discarding skill '%s' — quality gate failed "
                    "(total=%d, weakest=%s)",
                    name,
                    appraisal_verdict.total,
                    appraisal_verdict.weakest_axis,
                )
                return

        result = orchestrator.create_skill_fn(
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
