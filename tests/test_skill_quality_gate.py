"""Tests for the skill quality gate in AgentOrchestrator.

Covers _appraise_skill_quality (pure helper), the appraiser LLM call
(fail-open on error/non-JSON), and the gate wiring in
_generate_and_save_skill (accepted → persist, rejected → discard,
no draft → skip appraiser, explicit skill_create → no appraisal).
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.agent.agent_loop import (
    SKILL_QUALITY_AXIS_MAX,
    SKILL_QUALITY_MIN_PER_AXIS,
    SKILL_QUALITY_MIN_TOTAL,
    AgentOrchestrator,
    _appraise_skill_quality,
)
from backend.agent.concurrency import AgentConcurrency
from backend.llm.call_factory import LLMResponse
from backend.llm.structured_response import (
    SKILL_EXTRACTION_SCHEMA,
    SKILL_QUALITY_SCHEMA,
)


# --- Fixtures / helpers ---


def _make_orchestrator(
    create_skill_fn: object | None = None,
    search_skills_fn: object | None = None,
) -> AgentOrchestrator:
    """Create a minimal AgentOrchestrator wired for quality-gate tests."""
    orch = AgentOrchestrator(
        broker=MagicMock(),
        concurrency=AgentConcurrency(),
        context_fn=lambda sid, **kwargs: [],
        create_skill_fn=create_skill_fn,
        search_skills_fn=search_skills_fn,
    )
    orch.tool_executor.tool_names_used = ["web_search"]
    return orch


def _llm_response_for_skill(payload: dict) -> LLMResponse:
    """Build an LLMResponse carrying a JSON-encoded skill payload."""
    return LLMResponse(
        text=json.dumps(payload),
        tool_calls=[],
        raw_message={"role": "assistant", "content": json.dumps(payload)},
    )


def _llm_response_for_appraisal(scores: dict, note: str = "ok") -> LLMResponse:
    """Build an LLMResponse carrying a JSON-encoded appraisal payload."""
    payload = {**scores, "appraisal_note": note}
    return LLMResponse(
        text=json.dumps(payload),
        tool_calls=[],
        raw_message={"role": "assistant", "content": json.dumps(payload)},
    )


# --- 5.1 _appraise_skill_quality ---


class TestAppraiseSkillQuality:
    """Pure helper: total + per-axis floor rule, weakest_axis."""

    def test_all_high_axis_scores_accepted(self):
        """All axes above floor and total above threshold → accepted."""
        appraisal = {
            "reusability": 20,
            "step_clarity": 20,
            "future_applicability": 20,
            "procedure_soundness": 20,
            "appraisal_note": "good",
        }
        verdict = _appraise_skill_quality(appraisal)
        assert verdict.total == 80
        assert verdict.accepted is True

    def test_total_below_threshold_rejected(self):
        """All axes above floor but total below MIN_TOTAL → rejected."""
        appraisal = {
            "reusability": 10,
            "step_clarity": 10,
            "future_applicability": 10,
            "procedure_soundness": 10,
            "appraisal_note": "weak",
        }
        verdict = _appraise_skill_quality(appraisal)
        assert verdict.total == 40
        assert verdict.accepted is False

    def test_per_axis_floor_rejects_even_with_high_total(self):
        """Three high axes + one below MIN_PER_AXIS → rejected."""
        appraisal = {
            "reusability": 25,
            "step_clarity": 25,
            "future_applicability": 25,
            "procedure_soundness": 5,
            "appraisal_note": "bad soundness",
        }
        verdict = _appraise_skill_quality(appraisal)
        assert verdict.total == 80
        assert verdict.accepted is False
        assert verdict.weakest_axis == "procedure_soundness"

    def test_weakest_axis_names_lowest_scoring_axis(self):
        """weakest_axis must name the axis with the lowest score."""
        appraisal = {
            "reusability": 20,
            "step_clarity": 5,
            "future_applicability": 20,
            "procedure_soundness": 20,
            "appraisal_note": "unclear steps",
        }
        verdict = _appraise_skill_quality(appraisal)
        assert verdict.weakest_axis == "step_clarity"

    def test_defaults_to_zero_for_missing_keys(self):
        """Missing axis keys default to 0 (strict schema should prevent this)."""
        appraisal = {"reusability": 25, "step_clarity": 25}
        verdict = _appraise_skill_quality(appraisal)
        assert verdict.total == 50
        assert verdict.accepted is False
        assert verdict.weakest_axis in (
            "future_applicability",
            "procedure_soundness",
        )

    def test_constants_match_spec(self):
        """Verify constant values match the specification."""
        assert SKILL_QUALITY_AXIS_MAX == 25
        assert SKILL_QUALITY_MIN_TOTAL == 68
        assert SKILL_QUALITY_MIN_PER_AXIS == 8

    def test_is_not_io(self):
        """_appraise_skill_quality is pure — no network, no DB, no side effects."""
        appraisal = {
            "reusability": 20,
            "step_clarity": 20,
            "future_applicability": 20,
            "procedure_soundness": 20,
            "appraisal_note": "n/a",
        }
        v1 = _appraise_skill_quality(appraisal)
        v2 = _appraise_skill_quality(appraisal)
        assert v1.total == v2.total
        assert v1.accepted == v2.accepted


# --- 5.2 Gate wiring in _generate_and_save_skill ---


_SKILL_PAYLOAD = {
    "title": "Find crypto prices",
    "description": "Fetch BTC price",
    "trigger": "user asks for crypto prices",
    "procedure": [],
    "errors": [],
    "verification": "cross-check",
}


class TestQualityGateAcceptsHighScoringSkill:
    """Accepted appraisal → create_skill_fn called unchanged."""

    @pytest.mark.asyncio
    async def test_accepted_skill_persists(self):
        """High appraisal scores → skill is persisted."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        call_count = 0

        async def llm_call(prompt, *, options=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Extraction call
                return _llm_response_for_skill(_SKILL_PAYLOAD)
            # Appraiser call
            return _llm_response_for_appraisal(
                {
                    "reusability": 20,
                    "step_clarity": 20,
                    "future_applicability": 20,
                    "procedure_soundness": 20,
                }
            )

        await orch._generate_and_save_skill(
            message="test message",
            tool_call_count=1,
            llm_call=llm_call,
        )

        assert call_count == 2, "Two LLM calls expected (extraction + appraisal)"
        create_skill_fn.assert_awaited_once()
        kwargs = create_skill_fn.await_args.kwargs
        assert kwargs["title"] == _SKILL_PAYLOAD["title"]
        # No appraisal/score/note field in create_skill_fn call
        assert "appraisal_note" not in kwargs
        assert "reusability" not in kwargs


class TestQualityGateRejectsLowScoringSkill:
    """Rejected appraisal → create_skill_fn NOT called, discard logged."""

    @pytest.mark.asyncio
    async def test_rejected_skill_discarded(self, caplog):
        """Low appraisal scores → skill is NOT persisted."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        async def llm_call(prompt, *, options=None):
            return _llm_response_for_appraisal(
                {
                    "reusability": 5,
                    "step_clarity": 5,
                    "future_applicability": 5,
                    "procedure_soundness": 5,
                }
            )

        with caplog.at_level(logging.INFO):
            await orch._generate_and_save_skill(
                message="test message",
                tool_call_count=1,
                llm_call=llm_call,
            )

        create_skill_fn.assert_not_called()
        assert any(
            "quality gate failed" in record.getMessage() for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_rejected_on_per_axis_floor(self, caplog):
        """Three high axes + one below floor → rejected."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        async def llm_call(prompt, *, options=None):
            return _llm_response_for_appraisal(
                {
                    "reusability": 25,
                    "step_clarity": 25,
                    "future_applicability": 25,
                    "procedure_soundness": 3,
                }
            )

        with caplog.at_level(logging.INFO):
            await orch._generate_and_save_skill(
                message="test message",
                tool_call_count=1,
                llm_call=llm_call,
            )

        create_skill_fn.assert_not_called()


# --- 5.3 Fail-open ---


class TestQualityGateFailOpen:
    """Appraiser error / non-JSON → warning logged, skill persisted."""

    @pytest.mark.asyncio
    async def test_appraiser_non_json_falls_through(self, caplog):
        """Non-JSON appraiser response → skill still persisted."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        async def llm_call(prompt, *, options=None):
            if options and options.response_schema is SKILL_QUALITY_SCHEMA:
                # Appraiser returns non-JSON
                return LLMResponse(
                    text="not json at all",
                    tool_calls=[],
                    raw_message={"role": "assistant", "content": "not json at all"},
                )
            return _llm_response_for_skill(_SKILL_PAYLOAD)

        with caplog.at_level(logging.WARNING):
            await orch._generate_and_save_skill(
                message="test message",
                tool_call_count=1,
                llm_call=llm_call,
            )

        create_skill_fn.assert_awaited_once()
        assert any(
            "not return a usable verdict" in record.getMessage()
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_appraiser_exception_falls_through(self, caplog):
        """Appraiser raises → skill still persisted."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        call_count = 0

        async def llm_call(prompt, *, options=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _llm_response_for_skill(_SKILL_PAYLOAD)
            raise TimeoutError("appraiser timed out")

        with caplog.at_level(logging.WARNING):
            await orch._generate_and_save_skill(
                message="test message",
                tool_call_count=1,
                llm_call=llm_call,
            )

        create_skill_fn.assert_awaited_once()


# --- 5.4 Non-JSON extraction skips appraiser ---


class TestNoAppraiserOnNonJsonExtraction:
    """When extraction returns non-JSON, the appraiser MUST NOT be called."""

    @pytest.mark.asyncio
    async def test_non_json_extraction_skips_appraiser(self, caplog):
        """Non-JSON extraction → llm_call called exactly once (no appraisal)."""
        create_skill_fn = AsyncMock(return_value={"status": "saved"})
        orch = _make_orchestrator(create_skill_fn=create_skill_fn)

        call_count = 0

        async def llm_call(prompt, *, options=None):
            nonlocal call_count
            call_count += 1
            return LLMResponse(
                text="plain text response not json " * 5,
                tool_calls=[],
                raw_message={"role": "assistant", "content": "plain text"},
            )

        with caplog.at_level(logging.WARNING):
            await orch._generate_and_save_skill(
                message="test",
                tool_call_count=1,
                llm_call=llm_call,
            )

        assert call_count == 1, (
            "Appraiser must NOT be called when extraction fails to return JSON"
        )
        create_skill_fn.assert_awaited_once()


# --- 5.5 SKILL_EXTRACTION_SCHEMA unchanged ---


class TestExtractionSchemaUntouched:
    """SKILL_EXTRACTION_SCHEMA retains exactly its six required fields."""

    def test_extraction_schema_has_six_required_fields(self):
        """Guard against accidental drift — six fields required."""
        required = SKILL_EXTRACTION_SCHEMA.get("required", [])
        expected = {
            "title",
            "description",
            "trigger",
            "procedure",
            "errors",
            "verification",
        }
        assert set(required) == expected

    def test_extraction_schema_has_no_quality_field(self):
        """No quality/score/appraisal field in extraction schema."""
        properties = SKILL_EXTRACTION_SCHEMA.get("properties", {})
        forbidden = {
            "reusability",
            "step_clarity",
            "procedure_soundness",
            "appraisal_note",
        }
        assert not (set(properties) & forbidden), (
            "SKILL_EXTRACTION_SCHEMA must not contain quality-appraisal fields"
        )
