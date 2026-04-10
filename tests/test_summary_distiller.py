"""Tests for Summary Distiller agent record synthesis.

Covers successful synthesis, invalid JSON handling,
validation failure, LLM error handling, and the full
process_summary pipeline.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from backend.llm.capabilities_registry import CapabilitiesRegistry
from backend.llm.compute_config import (
    ComputeConfig,
    ComputeMode,
)
from backend.llm.ollama.provider import OllamaProvider
from backend.memory.models import Record, Summary
from backend.memory.summary_distiller import (
    _build_relation_heuristics,
    _build_typed_relations,
    _compress_based_on_reasons,
    _filter_summary_fields,
    _infer_summary_scope,
    _is_duplicate_reason,
    _is_duplicate_summary_main_point,
    _is_overreaching_reversion_distillation,
    _is_overreaching_short_term_distillation,
    _parse_distillation,
    _should_downgrade_long_term_scope_by_time_signals,
    _summary_semantic_signature,
    process_summary,
    synthesize_observations,
)
from backend.security.content_filter import FilterResult


def _make_provider(transport):
    """Create a provider with mock transport and bare capabilities registry."""
    config = ComputeConfig(mode=ComputeMode.GPU_ONLY, gpu_layers=0)
    provider = OllamaProvider(
        base_url="http://localhost:11434",
        primary_model="qwen3:8b",
        dialog_recorder_model="qwen3:1.7b",
        compute_config=config,
        capabilities_registry=CapabilitiesRegistry(),
        num_ctx=8192,
    )
    provider._client = httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost:11434",
    )
    return provider


def _sample_observations():
    """Create sample records for testing."""
    return [
        Record(
            summary="User prefers Python",
            key_facts=["Uses Python 3.12"],
            action_items=[],
        ),
        Record(
            summary="User builds web apps",
            key_facts=["Uses FastAPI"],
            action_items=["Deploy to Docker"],
        ),
    ]


class TestSynthesizeRecords:
    """Tests for the synthesize_observations function."""

    async def test_valid_synthesis(self):
        """Verify records synthesize into summary."""
        summary_text = "\n".join(
            [
                "MAIN_POINT: User is a Python web developer",
                "TRUST_LEVEL: high",
                "SCOPE: long_term",
                "BASED_ON:",
                "- Uses Python 3.12",
                "- Uses FastAPI",
            ]
        )

        request_payload: dict[str, object] = {}

        async def handler(request):
            request_payload.update(json.loads(request.content.decode()))
            assert request_payload["model"] == "qwen3:1.7b"
            return httpx.Response(
                200,
                json={
                    "message": {"content": summary_text},
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = _make_provider(transport)

        ref = await synthesize_observations(provider, _sample_observations())
        assert ref is not None
        assert "Python" in ref.main_point
        assert ref.trust_level.value == "high"
        assert ref.scope.value == "long_term"
        sent_messages = request_payload["messages"]
        assert isinstance(sent_messages, list)
        assert (
            "describe the old state and the new state explicitly"
            in sent_messages[0]["content"]
        )
        user_content = sent_messages[1]["content"]
        assert "Key facts:" in user_content
        assert "Action items:" in user_content
        assert "[Record 1]" in user_content
        assert ref.record_relations == []
        assert "Deploy to Docker" in user_content
        await provider.close()

    async def test_invalid_json_returns_none(self):
        """Verify invalid JSON from LLM returns None."""

        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "message": {"content": "not json"},
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = _make_provider(transport)

        ref = await synthesize_observations(provider, _sample_observations())
        assert ref is None
        await provider.close()

    async def test_llm_error_returns_none(self):
        """Verify LLM error returns None."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        provider = _make_provider(transport)

        ref = await synthesize_observations(provider, _sample_observations())
        assert ref is None
        await provider.close()


class TestParseSummary:
    """Tests for _parse_distillation."""

    def test_valid_plain_text(self):
        """Verify valid plain-text output parses correctly."""
        raw = "\n".join(
            [
                "MAIN_POINT: test",
                "TRUST_LEVEL: medium",
                "SCOPE: short_term",
                "BASED_ON:",
                "- e1",
            ]
        )
        ref = _parse_distillation(raw)
        assert ref is not None
        assert ref.main_point == "test"
        assert ref.based_on == ["e1"]
        assert ref.trust_level.value == "medium"
        assert ref.scope.value == "short_term"

    def test_valid_json(self):
        """Verify legacy JSON output still parses correctly."""
        raw = json.dumps(
            {
                "main_point": "test",
                "based_on": ["e1"],
                "trust_level": "medium",
                "scope": "long_term",
                "record_relations": ["supports"],
            }
        )
        ref = _parse_distillation(raw)
        assert ref is not None
        assert ref.main_point == "test"
        assert ref.scope.value == "long_term"

    def test_invalid_json(self):
        """Verify invalid JSON returns None."""
        assert _parse_distillation("{bad") is None

    def test_invalid_trust_level(self):
        """Verify invalid trust level returns None."""
        raw = json.dumps(
            {
                "main_point": "test",
                "based_on": [],
                "trust_level": "unknown",
            }
        )
        assert _parse_distillation(raw) is None


def _make_distillation():
    """Build a Summary for tests."""
    return Summary(
        main_point="User prefers Python",
        based_on=["Uses Python 3.12"],
        trust_level="high",
        scope="long_term",
        record_relations=["supports"],
    )


def _clean_filter_result(text="User prefers Python"):
    """Build a FilterResult with no flags."""
    return FilterResult(text=text)


def _passthrough_filter_result(text: str) -> FilterResult:
    """Return a clean filter result preserving the original text."""
    return FilterResult(text=text)


def _injection_filter_result(text="User prefers Python"):
    """Build a FilterResult with injection flagged."""
    return FilterResult(
        text=text,
        injection_flagged=True,
        blocked=True,
    )


class TestSummaryReasonCleanup:
    """Tests for summary reason post-processing."""

    def test_duplicate_reason_detection_matches_subset_wording(self):
        """A shorter subset reason should count as a duplicate."""
        assert (
            _is_duplicate_reason(
                "User confirmed that concise answers are no longer active.",
                (
                    "User confirmed that concise answers are no longer the active "
                    "preference for technical help."
                ),
            )
            is True
        )

    def test_reason_cleanup_keeps_richer_duplicate_reason(self):
        """Near-duplicate reasons should collapse to the richer wording."""
        compressed = _compress_based_on_reasons(
            [
                "User confirmed that concise answers are no longer active.",
                (
                    "User confirmed that concise answers are no longer the "
                    "active preference for technical help."
                ),
                "Future debugging answers must include context and reasoning.",
            ]
        )

        assert compressed == [
            (
                "User confirmed that concise answers are no longer the active "
                "preference for technical help"
            ),
            "Future debugging answers must include context and reasoning",
        ]

    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=_passthrough_filter_result,
    )
    def test_filter_summary_fields_deduplicates_based_on(self, mock_filter_output):
        """Filtered summaries should store only non-duplicate reasons."""
        summary = Summary(
            main_point="User now prefers detailed debugging answers.",
            based_on=[
                "User confirmed that concise answers are no longer active.",
                (
                    "User confirmed that concise answers are no longer the "
                    "active preference for technical help."
                ),
                "Future debugging answers must include context and reasoning.",
            ],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        )

        filtered_distillation = _filter_summary_fields(summary)

        assert filtered_distillation is not None
        assert filtered_distillation.based_on == [
            (
                "User confirmed that concise answers are no longer the active "
                "preference for technical help"
            ),
            "Future debugging answers must include context and reasoning",
        ]

    def test_scope_inference_marks_memory_loop_summary_as_short_term(self):
        """Test-run summaries should be forced into short-term scope."""
        summary = Summary(
            main_point=(
                "The user is validating the biMEMO memory subsystems for "
                "test memory-loop-1-abc123."
            ),
            based_on=[
                "This memory test tracks the current validation run.",
            ],
            trust_level="medium",
            scope="long_term",
            record_relations=[],
        )

        assert _infer_summary_scope(summary).value == "short_term"

    def test_scope_inference_marks_action_only_task_summary_as_short_term(self):
        """Task-bound action summaries should be forced into short-term scope."""
        summary = Summary(
            main_point="The user is currently validating the memory pipeline.",
            based_on=["This work belongs to the active validation task."],
            trust_level="medium",
            scope="long_term",
            record_relations=[],
        )
        records = [
            Record(
                summary="User is validating the current memory test run.",
                key_facts=["Current validation task is active."],
                action_items=["Continue the current validation run."],
                memory_kinds=["action"],
            )
        ]

        assert _infer_summary_scope(summary, records).value == "short_term"

    def test_scope_inference_keeps_future_preference_long_term_with_session_tag(self):
        """Future-facing preference shifts must stay long-term despite session tags."""
        summary = Summary(
            main_point=(
                "For session memory-loop-1-abc123, the user now prefers "
                "detailed, step-by-step answers for future technical help."
            ),
            based_on=[
                (
                    "The new preference replaces the earlier concise style for "
                    "future debugging answers."
                ),
            ],
            trust_level="high",
            scope="short_term",
            record_relations=[],
        )
        records = [
            Record(
                summary=(
                    "The default technical-answer preference is now detailed "
                    "and step-by-step."
                ),
                key_facts=[
                    "Future debugging answers must include context and reasoning.",
                    "The temporary test context should stay separate from the "
                    "durable answer-style preference.",
                ],
                action_items=[],
                memory_kinds=["preference", "conflict"],
            )
        ]

        assert _infer_summary_scope(summary, records).value == "long_term"

    def test_short_term_summary_that_erases_durable_preference_is_rejected(self):
        """Temporary context must not wipe a future-facing preference signal."""
        summary = Summary(
            main_point=(
                "Nothing from this session should be remembered as long-term; "
                "everything here is temporary test context."
            ),
            based_on=[
                (
                    "The current validation run should not persist, so no durable "
                    "preference remains."
                ),
            ],
            trust_level="high",
            scope="short_term",
            record_relations=[],
        )
        records = [
            Record(
                summary=(
                    "The temporary test context should stay separate from the "
                    "durable answer-style preference."
                ),
                key_facts=[
                    "Future debugging answers must stay detailed.",
                    "The default technical-answer preference is now detailed "
                    "and step-by-step.",
                ],
                action_items=[],
                memory_kinds=["preference", "fact"],
            )
        ]

        assert _is_overreaching_short_term_distillation(summary, records)

    def test_long_term_scope_is_downgraded_when_temporary_language_conflicts(self):
        """Temporary preference wording must not survive as long-term scope."""
        records = [
            Record(
                summary=(
                    "User prefers detailed step-by-step answers for future "
                    "technical replies."
                ),
                key_facts=[
                    "Detailed answers should guide future technical replies.",
                ],
                action_items=[],
                memory_kinds=["preference"],
            )
        ]
        summary = Summary(
            main_point=(
                "The user's detailed answer style is temporary for this session "
                "and will not persist in future chats."
            ),
            based_on=[
                "The user will need to restate this preference later.",
            ],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        )

        assert (
            _should_downgrade_long_term_scope_by_time_signals(
                summary,
                records,
            )
            is True
        )

    def test_long_term_scope_is_downgraded_when_session_bound_language_conflicts(
        self,
    ):
        """Session-bound preference wording must not survive as long-term scope."""
        records = [
            Record(
                summary=(
                    "User prefers detailed step-by-step answers for future "
                    "technical replies."
                ),
                key_facts=[
                    "Detailed answers should guide future technical replies.",
                ],
                action_items=[],
                memory_kinds=["preference"],
            )
        ]
        summary = Summary(
            main_point=(
                "For this session, the user prefers detailed step-by-step answers "
                "with full context and reasoning."
            ),
            based_on=[
                "This style applies within the current session under session "
                "identifier memory-loop-1-12345678."
            ],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        )

        assert (
            _should_downgrade_long_term_scope_by_time_signals(
                summary,
                records,
            )
            is True
        )

    def test_detects_overreaching_reversion_distillation(self):
        """Late session recaps must not restore the old concise baseline."""
        summary = Summary.model_validate(
            {
                "main_point": (
                    "The current detailed, step-by-step style is strictly "
                    "temporary for this session. The original preference for "
                    "brief, direct, and concise technical answers remains "
                    "the default for all future sessions."
                ),
                "based_on": [
                    (
                        "Future sessions default to the concise style unless "
                        "the detailed preference is re-established."
                    ),
                ],
                "trust_level": "high",
                "scope": "long_term",
                "record_relations": [],
            }
        )
        records = [
            Record(
                summary=(
                    "The temporary test context should stay separate from the "
                    "durable answer-style preference."
                ),
                key_facts=[
                    "Future debugging answers must stay detailed.",
                    "The default technical-answer preference is now detailed "
                    "and step-by-step.",
                ],
                action_items=[],
                memory_kinds=["preference", "fact"],
            )
        ]

        assert _is_overreaching_reversion_distillation(summary, records)


class TestProcessSummary:
    """Tests for the process_summary pipeline."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock OllamaProvider."""
        return MagicMock(spec=OllamaProvider)

    @pytest.fixture
    def mock_db(self):
        """Create a mock DB proxy client."""
        db_client = MagicMock()
        db_client.fetch_untainted_records = AsyncMock(return_value=[])
        db_client.list_summaries_full = AsyncMock(return_value=[])
        db_client.save_summary = AsyncMock(return_value="ref-1")
        db_client.merge_summary_sources = AsyncMock(return_value=True)
        return db_client

    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=_passthrough_filter_result,
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=_make_distillation(),
    )
    async def test_happy_path(
        self,
        mock_synth,
        mock_fo,
        mock_provider,
        mock_db,
    ):
        """Verify full pipeline stores summary."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": "summary1",
                "key_facts": ["fact1"],
                "action_items": ["action1"],
                "memory_kinds": ["preference"],
            }
        ]
        result = await process_summary(provider=mock_provider, db_client=mock_db)
        assert result is not None
        mock_synth.assert_called_once()
        assert mock_fo.call_count == 2
        assert mock_fo.call_args_list[0].args == ("User prefers Python",)
        mock_db.fetch_untainted_records.assert_awaited_once()
        mock_db.save_summary.assert_awaited_once_with(
            main_point="User prefers Python",
            based_on=["Uses Python 3.12"],
            trust_level="high",
            summary_scope="long_term",
            source_record_ids=["obs-1"],
            record_relations=[{"record_id": "obs-1", "relation": "supports"}],
        )

    def test_build_typed_relations_falls_back_to_supports_on_length_mismatch(self):
        """Misaligned distiller relation output should fall back to supports."""
        typed_relations = _build_typed_relations(
            record_ids=["o1", "o2"],
            relation_values=[],
        )
        assert typed_relations == [
            {"record_id": "o1", "relation": "supports"},
            {"record_id": "o2", "relation": "supports"},
        ]

    def test_build_typed_relations_uses_distiller_labels(self):
        """Aligned distiller relation output should be preserved."""
        typed_relations = _build_typed_relations(
            record_ids=["o1", "o2"],
            relation_values=["supports", "outdates"],
        )
        assert typed_relations == [
            {"record_id": "o1", "relation": "supports"},
            {"record_id": "o2", "relation": "outdates"},
        ]

    def test_relation_heuristics_mark_old_concise_preference_as_outdated(self):
        """Old concise preference should become outdated."""
        records = [
            Record(
                summary="User prefers concise technical answers.",
                key_facts=["User prefers concise technical answers."],
                action_items=[],
            ),
            Record(
                summary="User now prefers detailed step-by-step answers.",
                key_facts=["User prefers detailed answers with context and reasoning."],
                action_items=[],
            ),
        ]
        summary = Summary(
            main_point=(
                "User now prefers detailed step-by-step answers with context "
                "and reasoning instead of concise technical answers."
            ),
            based_on=["Detailed answers are now active."],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        )

        relation_values = _build_relation_heuristics(records, summary)

        assert relation_values == [
            "outdates",
            "supports",
        ]

    def test_build_typed_relations_uses_heuristics_when_model_omits_labels(self):
        """Missing distiller labels should use the shift heuristic."""
        records = [
            Record(
                summary="User prefers concise technical answers.",
                key_facts=["User prefers concise technical answers."],
                action_items=[],
            ),
            Record(
                summary="User now prefers detailed step-by-step answers.",
                key_facts=["User prefers detailed answers with context and reasoning."],
                action_items=[],
            ),
        ]
        summary = Summary(
            main_point=(
                "User now prefers detailed step-by-step answers with context "
                "and reasoning instead of concise technical answers."
            ),
            based_on=["Detailed answers are now active."],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        )

        typed_relations = _build_typed_relations(
            record_ids=["o1", "o2"],
            relation_values=[],
            records=records,
            summary=summary,
        )

        assert typed_relations == [
            {"record_id": "o1", "relation": "outdates"},
            {"record_id": "o2", "relation": "supports"},
        ]

    def test_duplicate_summary_detection_matches_similar_main_points(self):
        """Similar summary main points should be treated as duplicates."""
        assert (
            _is_duplicate_summary_main_point(
                "User now prefers detailed step-by-step debugging answers.",
                [
                    "User prefers detailed step-by-step answers for debugging help.",
                ],
            )
            is True
        )

    def test_summary_semantic_signature_tracks_preference_shift(self):
        """Preference-shift summaries should produce a stable semantic signature."""
        assert _summary_semantic_signature(
            "The user shifted from concise technical answers to detailed "
            "step-by-step answers for future debugging help."
        ) == (
            "pref_shift_concise_to_detailed",
            "scope_debugging",
            "scope_future",
        )

    def test_duplicate_summary_detection_matches_cross_language_shift(self):
        """Same preference shift should deduplicate across different languages."""
        assert (
            _is_duplicate_summary_main_point(
                (
                    "The user shifted from concise technical answers to detailed "
                    "step-by-step answers for future debugging help."
                ),
                [
                    (
                        "Пользователь перешел от кратких технических ответов к "
                        "подробным пошаговым ответам для будущей отладки."
                    ),
                ],
            )
            is True
        )

    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=_passthrough_filter_result,
    )
    @patch(
        "backend.memory.summary_distiller.runtime._load_recent_summary_records",
        new_callable=AsyncMock,
        return_value=[
            {
                "id": "existing-summary-1",
                "main_point": (
                    "User prefers detailed step-by-step answers with context."
                ),
            },
        ],
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=Summary(
            main_point="User now prefers detailed step-by-step answers with context.",
            based_on=["Detailed answers are now active."],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        ),
    )
    async def test_duplicate_summary_is_merged_into_existing_memory(
        self,
        mock_synth,
        mock_recent_distillations,
        mock_filter_output,
        mock_provider,
        mock_db,
    ):
        """Duplicate summaries should extend the existing summary instead."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": "summary1",
                "key_facts": ["fact1"],
                "action_items": ["action1"],
                "memory_kinds": ["preference"],
            }
        ]
        result = await process_summary(provider=mock_provider, db_client=mock_db)
        assert result == "existing-summary-1"
        mock_db.merge_summary_sources.assert_awaited_once_with(
            "existing-summary-1",
            main_point="User now prefers detailed step-by-step answers with context.",
            based_on=["Detailed answers are now active"],
            trust_level="high",
            summary_scope="long_term",
            trust_level_score=3,
            source_record_ids=["obs-1"],
            record_relations=[{"record_id": "obs-1", "relation": "supports"}],
        )
        mock_db.save_summary.assert_not_called()

    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        return_value=_injection_filter_result(),
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=_make_distillation(),
    )
    async def test_output_injection_discards(
        self,
        mock_synth,
        mock_fo,
        mock_provider,
        mock_db,
    ):
        """Verify output injection discards summary."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": "summary1",
                "key_facts": ["fact1"],
                "action_items": ["action1"],
                "memory_kinds": ["preference"],
            }
        ]
        result = await process_summary(provider=mock_provider, db_client=mock_db)
        assert result is None
        mock_db.save_summary.assert_not_called()

    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=[
            _clean_filter_result("safe pattern"),
            _injection_filter_result("unsafe evidence"),
        ],
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=Summary(
            main_point="safe pattern",
            based_on=["unsafe evidence"],
            trust_level="high",
            scope="long_term",
        ),
    )
    async def test_based_on_injection_discards(
        self,
        mock_synth,
        mock_filter_output,
        mock_provider,
        mock_db,
    ):
        """Verify unsafe based_on text discards the summary."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": "summary1",
                "key_facts": ["fact1"],
                "action_items": ["action1"],
                "memory_kinds": ["preference"],
            }
        ]
        result = await process_summary(provider=mock_provider, db_client=mock_db)
        assert result is None
        assert mock_filter_output.call_count == 2
        mock_db.save_summary.assert_not_called()

    async def test_empty_untainted_returns_none(
        self,
        mock_provider,
        mock_db,
    ):
        """Verify empty record list returns None."""
        result = await process_summary(provider=mock_provider, db_client=mock_db)
        assert result is None
        mock_db.save_summary.assert_not_called()

    @patch("backend.memory.summary_distiller.runtime.append_distiller_trace")
    async def test_empty_untainted_writes_trace(
        self,
        mock_append_trace,
        mock_provider,
        mock_db,
    ):
        """Empty distiller input should still leave a diagnostic trace."""
        result = await process_summary(provider=mock_provider, db_client=mock_db)
        assert result is None
        mock_append_trace.assert_called_once()
        trace_entry = mock_append_trace.call_args.args[0]
        assert trace_entry["decision"] == "summary_skipped_by_no_eligible_observations"

    @patch("backend.memory.summary_distiller.runtime.append_distiller_trace")
    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=_passthrough_filter_result,
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=_make_distillation(),
    )
    async def test_stored_summary_writes_trace(
        self,
        mock_synth,
        mock_filter,
        mock_append_trace,
        mock_provider,
        mock_db,
    ):
        """Stored summaries should leave a trace with the final decision."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": "summary1",
                "key_facts": ["fact1"],
                "action_items": ["action1"],
                "memory_kinds": ["preference"],
            }
        ]
        result = await process_summary(provider=mock_provider, db_client=mock_db)
        assert result is not None
        assert mock_append_trace.call_count == 1
        trace_entry = mock_append_trace.call_args.args[0]
        assert trace_entry["decision"] == "summary_stored_by_llm_synthesis"
        assert trace_entry["summary_id"] == result

    @patch("backend.memory.summary_distiller.runtime.append_distiller_trace")
    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=_passthrough_filter_result,
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=Summary(
            main_point=(
                "Nothing from this session should be remembered as long-term; "
                "everything here is temporary test context."
            ),
            based_on=[
                "No durable preference remains after this validation run.",
            ],
            trust_level="high",
            scope="short_term",
            record_relations=[],
        ),
    )
    async def test_overreaching_short_term_summary_is_discarded(
        self,
        mock_synth,
        mock_filter,
        mock_append_trace,
        mock_provider,
        mock_db,
    ):
        """Short-term context must not erase a durable preference signal."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": (
                    "Temporary validation context should stay separate from "
                    "the durable answer-style preference."
                ),
                "key_facts": [
                    "Future debugging answers must stay detailed.",
                    "The default technical-answer preference is now "
                    "detailed and step-by-step.",
                ],
                "action_items": [],
                "memory_kinds": ["preference", "fact"],
            }
        ]
        result = await process_summary(provider=mock_provider, db_client=mock_db)

        assert result is None
        mock_db.save_summary.assert_not_called()
        trace_entry = mock_append_trace.call_args.args[0]
        assert trace_entry["decision"] == "summary_rejected_by_short_term_overreach"

    @patch(
        "backend.memory.summary_distiller.runtime.append_distiller_trace",
    )
    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=_passthrough_filter_result,
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=Summary(
            main_point=(
                "The user's detailed answer style is temporary for this "
                "session and will not persist in future chats."
            ),
            based_on=[
                "The user will need to restate this preference later.",
            ],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        ),
    )
    async def test_long_term_scope_downgrade_is_written_to_trace(
        self,
        mock_synth,
        mock_filter,
        mock_append_trace,
        mock_provider,
        mock_db,
    ):
        """Scope downgrade should be stored explicitly in the distiller trace."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": ("Detailed answers should guide future technical replies."),
                "key_facts": ["This preference should guide future technical answers."],
                "action_items": [],
                "memory_kinds": ["preference"],
            }
        ]

        result = await process_summary(provider=mock_provider, db_client=mock_db)

        assert result == "ref-1"
        mock_db.save_summary.assert_awaited_once()
        _, kwargs = mock_db.save_summary.await_args
        assert kwargs["summary_scope"] == "short_term"
        trace_entry = mock_append_trace.call_args.args[0]
        assert trace_entry["decision"] == "summary_stored_by_llm_synthesis"
        assert trace_entry["original_scope"] == "long_term"
        assert (
            trace_entry["scope_adjustment_reason"]
            == "scope_downgraded_by_time_signals_and_llm_mismatch"
        )

    @patch("backend.memory.summary_distiller.runtime.append_distiller_trace")
    @patch(
        "backend.memory.summary_distiller.runtime.filter_output",
        side_effect=_passthrough_filter_result,
    )
    @patch(
        "backend.memory.summary_distiller.runtime.synthesize_observations",
        new_callable=AsyncMock,
        return_value=Summary(
            main_point=(
                "The current detailed, step-by-step debugging style is "
                "strictly temporary and the user's original preference "
                "for brief, direct, and concise technical answers remains "
                "the default for all future sessions."
            ),
            based_on=[
                (
                    "Future sessions default to the concise style unless "
                    "the detailed preference is re-established."
                ),
            ],
            trust_level="high",
            scope="long_term",
            record_relations=[],
        ),
    )
    async def test_overreaching_reversion_summary_is_discarded(
        self,
        mock_synth,
        mock_filter,
        mock_append_trace,
        mock_provider,
        mock_db,
    ):
        """Late recap text must not restore the old concise baseline."""
        mock_db.fetch_untainted_records.return_value = [
            {
                "id": "obs-1",
                "summary": (
                    "The temporary test context should stay separate from the "
                    "durable answer-style preference."
                ),
                "key_facts": [
                    "Future debugging answers must stay detailed.",
                    "The default technical-answer preference is now "
                    "detailed and step-by-step.",
                ],
                "action_items": [],
                "memory_kinds": ["preference", "fact"],
            }
        ]
        result = await process_summary(provider=mock_provider, db_client=mock_db)

        assert result is None
        mock_db.save_summary.assert_not_called()
        trace_entry = mock_append_trace.call_args.args[0]
        assert (
            trace_entry["decision"]
            == "summary_rejected_by_preference_reversion_overreach"
        )
