"""Tests for the auto-skill extraction path in AgentOrchestrator.

Covers _generate_and_save_skill behaviour after the migration to
LLMCallOptions + SKILL_EXTRACTION_SCHEMA: the LLM call must carry the
correct options, a conforming response must populate all skill fields,
a non-JSON response must fall back safely, and a fresh duplicate must
short-circuit the LLM call.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.concurrency import AgentConcurrency
from backend.llm.call_factory import LLMResponse
from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.structured_response import SKILL_EXTRACTION_SCHEMA


def _make_orchestrator(
    create_skill_fn: object | None = None,
    search_skills_fn: object | None = None,
) -> AgentOrchestrator:
    """Create a minimal AgentOrchestrator wired for skill-extraction tests."""
    orch = AgentOrchestrator(
        broker=MagicMock(),
        concurrency=AgentConcurrency(),
        context_fn=lambda sid, **kwargs: [],
        create_skill_fn=create_skill_fn,
        search_skills_fn=search_skills_fn,
    )
    orch.tool_executor.tool_names_used = ["web_search", "web_search", "web_search"]
    return orch


def _llm_response_for_skill(payload: dict) -> LLMResponse:
    """Build an LLMResponse carrying a JSON-encoded skill payload."""
    return LLMResponse(
        text=json.dumps(payload),
        tool_calls=[],
        raw_message={"role": "assistant", "content": json.dumps(payload)},
    )


class TestSkillExtractionCallsLLMWithOptions:
    """The LLM call must carry the skill-extraction options."""

    @pytest.mark.asyncio
    async def test_options_force_json_schema_and_skill_schema(self):
        """Verify the call passes JSON_SCHEMA with SKILL_EXTRACTION_SCHEMA."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        captured: dict = {}

        async def llm_call(prompt, *, options=None):
            captured["prompt"] = prompt
            captured["options"] = options
            return _llm_response_for_skill(
                {
                    "title": "t",
                    "description": "d",
                    "trigger": "tr",
                    "procedure": [],
                    "errors": [],
                    "verification": "v",
                }
            )

        await orch._generate_and_save_skill(
            message="test message",
            tool_call_count=3,
            llm_call=llm_call,
        )

        options = captured["options"]
        assert isinstance(options, LLMCallOptions)
        assert options.response_format is LlmResponseFormat.JSON_SCHEMA
        assert options.response_schema is SKILL_EXTRACTION_SCHEMA
        assert options.tools == []


class TestSkillExtractionConformingResponse:
    """A conforming response must populate all six skill fields."""

    @pytest.mark.asyncio
    async def test_all_fields_populated_from_json(self):
        """Verify parsed fields reach create_skill_fn keyword arguments."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)
        payload = {
            "title": "Find crypto prices",
            "description": "Fetch BTC/ETH/SOL in USD",
            "trigger": "user asks for crypto prices",
            "procedure": [
                {"tool": "web_search", "query": "BTC price USD", "note": ""},
                {"tool": "web_search", "query": "ETH price USD", "note": ""},
            ],
            "errors": [
                {"name": "stale price", "fix": "retry with timestamp filter"},
            ],
            "verification": "cross-check two sources",
        }

        async def llm_call(prompt, *, options=None):
            return _llm_response_for_skill(payload)

        await orch._generate_and_save_skill(
            message="Найди курсы",
            tool_call_count=2,
            llm_call=llm_call,
        )

        create_skill_fn.assert_awaited_once()
        kwargs = create_skill_fn.await_args.kwargs
        assert kwargs["title"] == payload["title"]
        assert kwargs["description"] == payload["description"]
        assert kwargs["trigger"] == payload["trigger"]
        assert json.loads(kwargs["procedure"]) == payload["procedure"]
        assert json.loads(kwargs["errors"]) == payload["errors"]
        assert kwargs["verification"] == payload["verification"]


class TestSkillExtractionFallback:
    """A non-JSON response must fall back to trigger_text and log a warning."""

    @pytest.mark.asyncio
    async def test_non_json_text_goes_to_trigger(self, caplog):
        """Verify raw text lands in trigger when JSON parsing fails."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)
        raw_text = "Not a JSON object at all, just plain text " * 2

        async def llm_call(prompt, *, options=None):
            return LLMResponse(
                text=raw_text,
                tool_calls=[],
                raw_message={"role": "assistant", "content": raw_text},
            )

        with caplog.at_level(logging.WARNING):
            await orch._generate_and_save_skill(
                message="test",
                tool_call_count=1,
                llm_call=llm_call,
            )

        create_skill_fn.assert_awaited_once()
        kwargs = create_skill_fn.await_args.kwargs
        assert kwargs["trigger"] == raw_text
        assert any(
            "not JSON despite schema" in record.getMessage()
            for record in caplog.records
        )


class TestSkillExtractionEmptyResponse:
    """An empty or too-short response must skip skill creation."""

    @pytest.mark.asyncio
    async def test_empty_response_does_not_call_create_skill(self, caplog):
        """Verify create_skill_fn is never invoked on empty LLM text."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        async def llm_call(prompt, *, options=None):
            return LLMResponse(
                text="",
                tool_calls=[],
                raw_message={"role": "assistant", "content": ""},
            )

        with caplog.at_level(logging.WARNING):
            await orch._generate_and_save_skill(
                message="test",
                tool_call_count=1,
                llm_call=llm_call,
            )

        create_skill_fn.assert_not_called()
        assert any("empty response" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_short_response_does_not_call_create_skill(self):
        """Verify <20-char responses are treated as empty."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        async def llm_call(prompt, *, options=None):
            return LLMResponse(
                text="{}",
                tool_calls=[],
                raw_message={"role": "assistant", "content": "{}"},
            )

        await orch._generate_and_save_skill(
            message="test",
            tool_call_count=1,
            llm_call=llm_call,
        )

        create_skill_fn.assert_not_called()


class TestSkillExtractionFreshDuplicateSkipsLLM:
    """A fresh duplicate skill must block the LLM call entirely."""

    @pytest.mark.asyncio
    async def test_fresh_duplicate_short_circuits(self):
        """Verify llm_call is not invoked when a fresh skill matches."""
        from datetime import UTC, datetime

        fresh_skill = {
            "name": "cached-skill",
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }
        search_skills_fn = MagicMock(return_value=[fresh_skill])
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(
            create_skill_fn=create_skill_fn,
            search_skills_fn=search_skills_fn,
        )

        llm_call = AsyncMock()

        await orch._generate_and_save_skill(
            message="any",
            tool_call_count=1,
            llm_call=llm_call,
        )

        llm_call.assert_not_awaited()
        create_skill_fn.assert_not_called()
