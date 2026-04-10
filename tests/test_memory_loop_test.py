"""Unit tests for the memory loop evaluation script."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from itertools import count
from pathlib import Path
from urllib.error import HTTPError


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "memory_loop_test.py"
MODULE_NAME = "memory_loop_test"


def _load_script_module():
    """Load the loop-test script as an importable module."""
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]

    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


module = _load_script_module()


def _empty_phase_breakdown() -> dict[str, object]:
    """Build an empty phase breakdown for compact ScenarioReport fixtures."""
    return {
        phase_name: module.PhaseSummary(
            dialog_step_count=0,
            avg_latency_seconds=0.0,
            avg_retrieved_memories=0.0,
            avg_retrieved_messages=0.0,
            current_style_response_rate=0.0,
            old_style_only_response_rate=0.0,
            error_count=0,
        )
        for phase_name in module.PHASE_NAMES
    }


class TestScenarioFiltering:
    """Verify scenario filtering and response-style helpers."""

    def test_api_client_initializes_http_opener(self):
        """API client initialization should create an opener in slots mode."""
        client = module.APIClient("http://127.0.0.1:5273")

        assert client._opener is not None

    def test_response_prefers_current_style(self):
        """Current preference markers should pass the final-style check."""
        assert module.response_prefers_current_style(
            "You now prefer detailed step-by-step answers.",
        )

    def test_response_only_mentions_old_style(self):
        """Old-style-only recall should be treated as a regression."""
        assert module.response_only_mentions_old_style(
            "You prefer concise and short answers.",
        )

    def test_response_has_old_style_as_active_long_term(self):
        """Final long-term recall should fail when old style stays active."""
        assert module.response_has_old_style_as_active_long_term(
            "Final active long-term preference: concise, brief technical answers.",
        )

    def test_response_allows_old_style_when_explicitly_outdated(self):
        """Old style should be allowed when it is clearly marked as outdated."""
        assert not module.response_has_old_style_as_active_long_term(
            "The long-term preference is detailed and step-by-step; the earlier "
            "concise style is no longer active.",
        )

    def test_response_rejects_old_style_even_when_detailed_is_only_an_exception(self):
        """An old default with detailed answers only on request must still fail."""
        assert module.response_has_old_style_as_active_long_term(
            "Long-term preference: brief, concise responses unless you explicitly "
            "request detailed explanations.",
        )

    def test_response_confirms_current_long_term_style(self):
        """Final long-term recall should require the new style to stay active."""
        assert module.response_confirms_current_long_term_style(
            "Final active long-term preference: detailed, step-by-step technical "
            "answers with context and reasoning.",
        )

    def test_response_confirms_bulleted_current_long_term_style(self):
        """Bullet-form final recall should still count as a correct pass."""
        assert module.response_confirms_current_long_term_style(
            "Final active long-term preference:\n"
            "- Detailed, step-by-step answers with context and reasoning.\n\n"
            "Temporary short-term context:\n"
            "- Validation run identifier.",
        )

    def test_response_rejects_short_term_only_heading(self):
        """Short-term-only headings should not satisfy long-term recall."""
        assert not module.response_confirms_current_long_term_style(
            "Current active preferences:\n\n"
            "**Short-term (session-scoped):**\n"
            "- Detailed, step-by-step technical answers with context.\n"
            "- Validation run identifier.\n",
        )

    def test_response_allows_long_term_block_with_old_style_in_comparison(self):
        """Old-style comparison text inside the long-term block should not fail."""
        response_text = (
            "## Long-Term Persistent Preference\n\n"
            "The core persistent preference established for this session is "
            "**detailed, step-by-step answers with full context and reasoning** "
            "for all technical and debugging questions. This includes:\n\n"
            "- Comprehensive explanations rather than concise or brief "
            "responses\n"
            "- Full reasoning shown for each step in the thought process\n"
            "- Depth and context prioritized over speed and brevity\n\n"
            "## Temporary Short-Term Context\n\n"
            "The temporary context is identified by session identifier "
            "**memory-loop-2-364723d3**."
        )

        assert module.response_confirms_current_long_term_style(response_text)
        assert not module.response_has_old_style_as_active_long_term(response_text)

    def test_response_allows_short_term_section_with_old_style_reference(self):
        """Short-term recap text should not poison long-term validation."""
        response_text = (
            "## Long-Term Persistent Preference\n\n"
            "The core persistent preference established for this session is "
            "**detailed, step-by-step answers with full context and reasoning** "
            "for all technical and debugging questions.\n\n"
            "## Temporary Short-Term Context\n\n"
            "The temporary context includes the test run identifier and the "
            "earlier brief/concise style used only for validation.\n"
        )

        assert module.response_confirms_current_long_term_style(response_text)
        assert not module.response_has_old_style_as_active_long_term(response_text)

    def test_default_scenario_exceeds_reflection_threshold(self):
        """Default scenario should be long enough to trigger distiller work."""
        prompts = module.build_scenario_prompts("scenario-tag")

        assert len(prompts) >= (
            module.DISTILLATION_TRIGGER_THRESHOLD + module.SCENARIO_DISTILLATION_BUFFER
        )

    def test_realistic_scenario_exceeds_summary_threshold(self):
        """Realistic scenario should stay long enough for distiller coverage."""
        prompts = module.build_scenario_prompts(
            "scenario-tag",
            scenario_profile="realistic",
        )

        assert len(prompts) >= (
            module.DISTILLATION_TRIGGER_THRESHOLD + module.SCENARIO_DISTILLATION_BUFFER
        )
        assert len(prompts) == 100

    def test_realistic_scenario_renders_natural_language_prompts(self):
        """Realistic profile should use more natural phrasing than control mode."""
        prompts = module.build_scenario_prompts(
            "scenario-tag",
            scenario_profile="realistic",
        )

        assert prompts[0] == "For this test run, remember the identifier scenario-tag."
        assert any(
            prompt == "I changed my mind: I want detailed step-by-step answers now."
            for prompt in prompts
        )

    def test_is_rate_limit_error_detects_chat_429(self):
        """Rate-limit helper should detect HTTP 429 failures."""
        error = RuntimeError("POST /chat -> 429: Too Many Requests")

        assert module.is_rate_limit_error(error)

    def test_api_client_reauthenticates_after_401(self, monkeypatch):
        """API client should retry once after re-authentication on 401."""

        class FakeHTTPResponse:
            """Simple context-managed response stub."""

            def __init__(self, body: str) -> None:
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return self._body.encode()

            def close(self) -> None:
                """Close the stub response."""

        class FakeOpener:
            """Opener that fails once with 401 and then succeeds."""

            def __init__(self) -> None:
                self.calls = 0

            def open(self, request, timeout=30):
                self.calls += 1
                if self.calls == 1:
                    raise HTTPError(
                        request.full_url,
                        401,
                        "Unauthorized",
                        hdrs=None,
                        fp=FakeHTTPResponse('{"detail":"Authentication required"}'),
                    )
                return FakeHTTPResponse('{"messages":[]}')

        reauth_calls: list[str] = []

        def fake_authenticate(client, password: str) -> None:
            reauth_calls.append(password)
            client.csrf_token = "fresh-csrf"

        monkeypatch.setattr(module, "authenticate", fake_authenticate)

        client = module.APIClient("http://127.0.0.1:5273", auth_password="pw")
        client._opener = FakeOpener()

        payload = client.get("/admin/memory/messages")

        assert payload == {"messages": []}
        assert reauth_calls == ["pw"]

    def test_api_client_retries_after_429(self, monkeypatch):
        """API client should back off and retry rate-limited requests."""

        class FakeHTTPResponse:
            """Simple context-managed response stub."""

            def __init__(self, body: str) -> None:
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return self._body.encode()

            def close(self) -> None:
                """Close the stub response."""

        class FakeOpener:
            """Opener that fails once with 429 and then succeeds."""

            def __init__(self) -> None:
                self.calls = 0

            def open(self, request, timeout=30):
                self.calls += 1
                if self.calls == 1:
                    raise HTTPError(
                        request.full_url,
                        429,
                        "Too Many Requests",
                        hdrs=None,
                        fp=FakeHTTPResponse('{"detail":"Rate limit exceeded"}'),
                    )
                return FakeHTTPResponse('{"summaries":[]}')

        sleep_calls: list[float] = []

        monkeypatch.setattr(module.time, "sleep", sleep_calls.append)

        client = module.APIClient("http://127.0.0.1:5273")
        client._opener = FakeOpener()

        payload = client.get("/admin/memory/summaries")

        assert payload == {"summaries": []}
        assert sleep_calls == [module.RATE_LIMIT_BACKOFF_SECONDS]

    def test_authenticate_with_runtime_fallback_uses_container_url(
        self,
        monkeypatch,
    ):
        """Default CLI URL should fall back to the container-local API."""
        calls: list[str] = []

        def fake_authenticate(client, password: str) -> None:
            calls.append(client.base_url)
            if client.base_url == module.DEFAULT_BASE_URL:
                raise RuntimeError("Connection refused")
            client.csrf_token = "csrf"

        monkeypatch.setattr(module, "authenticate", fake_authenticate)

        client = module.authenticate_with_runtime_fallback(
            module.DEFAULT_BASE_URL,
            "pw",
        )

        assert client.base_url == module.CONTAINER_LOCAL_BASE_URL
        assert calls == [
            module.DEFAULT_BASE_URL,
            module.CONTAINER_LOCAL_BASE_URL,
        ]

    def test_collect_memory_loop_conversation_ids(self):
        """Only memory-loop conversations should be selected for cleanup."""
        conversation_ids = module._collect_memory_loop_conversation_ids(
            [
                {"conversation_id": "memory-loop-1-abc"},
                {"conversation_id": "memory-loop-2-def"},
                {"conversation_id": "chat-123"},
                {"conversation_id": "memory-loop-1-abc"},
            ]
        )

        assert conversation_ids == [
            "memory-loop-1-abc",
            "memory-loop-2-def",
        ]

    def test_cleanup_old_memory_loop_conversations(self, monkeypatch):
        """Cleanup should delete prior memory-loop conversations only."""
        deleted_paths: list[tuple[str, dict[str, str] | None]] = []

        class FakeClient:
            """Minimal fake API client for cleanup tests."""

            def post(self, path: str, body=None, timeout=300):
                assert path == "/admin/reauth"
                assert body == {"password": "pw"}
                return {"reauth_token": "reauth-token"}

            def delete(self, path: str, timeout=30, extra_headers=None):
                deleted_paths.append((path, extra_headers))
                return {"detail": "Conversation deleted"}

        monkeypatch.setattr(
            module,
            "collect_paginated_entries",
            lambda *_args, **_kwargs: [
                {"conversation_id": "memory-loop-1-abc"},
                {"conversation_id": "chat-123"},
                {"conversation_id": "memory-loop-2-def"},
            ],
        )

        deleted_count = module.cleanup_old_memory_loop_conversations(
            FakeClient(),
            "pw",
        )

        assert deleted_count == 2
        assert deleted_paths == [
            (
                "/admin/conversations/memory-loop-1-abc",
                {"X-Reauth-Token": "reauth-token"},
            ),
            (
                "/admin/conversations/memory-loop-2-def",
                {"X-Reauth-Token": "reauth-token"},
            ),
        ]

    def test_parse_args_can_keep_existing_memory_loop_conversations(
        self,
        monkeypatch,
    ):
        """CLI should support accumulating runs on the existing database."""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "memory_loop_test.py",
                "--base-url",
                "http://127.0.0.1:8000",
                "--password",
                "pw",
                "--keep-existing-memory-loop-conversations",
            ],
        )

        arguments = module.parse_args()

        assert arguments.keep_existing_memory_loop_conversations is True

    def test_parse_args_defaults_to_single_iteration(self, monkeypatch):
        """CLI should run one iteration by default unless a series is requested."""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "memory_loop_test.py",
                "--password",
                "pw",
            ],
        )

        args = module.parse_args()

        assert args.max_iterations == 1

    def test_main_prints_series_notice_only_for_explicit_multi_run(
        self,
        monkeypatch,
        capsys,
    ):
        """Series notice should appear only when the caller explicitly asks for it."""
        monkeypatch.setattr(
            module,
            "parse_args",
            lambda: argparse.Namespace(
                base_url=module.DEFAULT_BASE_URL,
                password="pw",
                max_iterations=2,
                scenario_profile=module.DEFAULT_SCENARIO_PROFILE,
                chat_delay=0.0,
                pipeline_timeout=1,
                poll_interval=1,
                output_json="",
                stop_on_pass=False,
                keep_existing_memory_loop_conversations=False,
            ),
        )
        monkeypatch.setattr(
            module,
            "authenticate_with_runtime_fallback",
            lambda *_args, **_kwargs: object(),
        )
        monkeypatch.setattr(
            module,
            "cleanup_old_memory_loop_conversations",
            lambda *_args, **_kwargs: 0,
        )
        iteration_counter = count(1)
        monkeypatch.setattr(
            module,
            "run_iteration",
            lambda *_args, **_kwargs: module.ScenarioReport(
                scenario_tag=f"scenario-{next(iteration_counter)}",
                conversation_id="conv",
                passed=True,
                findings=[],
                recommendations=[],
                dialog_step_results=[],
                quality_score=98,
                scenario_counts={"messages": 200, "records": 10, "summaries": 1},
                duplication_report={
                    "duplicate_observation_count": 0,
                    "distillation_merge_count": 0,
                    "stored_distillation_count": 1,
                },
                contamination_report={
                    "long_term_summary_count": 0.0,
                    "contaminated_long_term_summary_count": 0.0,
                    "contaminated_long_term_summary_share": 0.0,
                },
                phase_breakdown=_empty_phase_breakdown(),
                first_record_elapsed_seconds=0.0,
                stabilization_elapsed_seconds=1.0,
            ),
        )

        exit_code = module.main()
        captured = capsys.readouterr().out

        assert exit_code == 0
        assert "Running an explicit multi-iteration series: 2 iterations" in captured


class TestScenarioEvaluation:
    """Verify report generation and recommendations."""

    def test_build_retrieval_report_highlights_dominant_memory_summary(self):
        """Retrieved memory reuse diagnostics should expose dominant summaries."""
        dialog_step_results = [
            module.ChatDialogStepResult(
                prompt="p1",
                response_text="detailed",
                elapsed_seconds=1.0,
                retrieved_memories=[
                    {"summary": "Preference: detailed answers"},
                    {"summary": "Preference: detailed answers"},
                ],
                retrieved_messages=[],
            ),
            module.ChatDialogStepResult(
                prompt="p2",
                response_text="detailed",
                elapsed_seconds=1.0,
                retrieved_memories=[
                    {"summary": "Preference: detailed answers"},
                    {"summary": "Temporary validation run"},
                ],
                retrieved_messages=[],
            ),
            module.ChatDialogStepResult(
                prompt="p3",
                response_text="detailed",
                elapsed_seconds=1.0,
                retrieved_memories=[
                    {"summary": "Preference: detailed answers"},
                ],
                retrieved_messages=[],
            ),
        ]

        report = module.build_retrieval_report(dialog_step_results)

        assert report["retrieved_memory_hits"] == 5
        assert report["unique_retrieved_memory_summaries"] == 2
        assert report["dominant_summary"] == "Preference: detailed answers"
        assert report["dominant_summary_hits"] == 4
        assert report["dominant_summary_share"] == 0.8

    def test_summarize_reports_returns_min_max_and_average(self):
        """Series summary should make run-to-run spread easy to inspect."""
        reports = [
            module.ScenarioReport(
                scenario_tag="scenario-1",
                conversation_id="conv-1",
                passed=True,
                findings=[],
                recommendations=[],
                dialog_step_results=[],
                quality_score=96,
                scenario_counts={"messages": 48, "records": 18, "summaries": 3},
                duplication_report={
                    "duplicate_observation_count": 2,
                    "distillation_merge_count": 1,
                    "stored_distillation_count": 3,
                },
                contamination_report={
                    "long_term_summary_count": 3.0,
                    "contaminated_long_term_summary_count": 1.0,
                    "contaminated_long_term_summary_share": 1 / 3,
                },
                retrieval_report={
                    "retrieved_memory_hits": 6,
                    "unique_retrieved_memory_summaries": 2,
                    "dominant_summary": "Detailed preference",
                    "dominant_summary_hits": 4,
                    "dominant_summary_share": 4 / 6,
                    "late_phase_retrieved_memory_hits": 3,
                    "late_phase_unique_retrieved_memory_summaries": 1,
                    "late_phase_dominant_summary": "Detailed preference",
                    "late_phase_dominant_summary_hits": 3,
                    "late_phase_dominant_summary_share": 1.0,
                },
                phase_breakdown={
                    "early": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=1.0,
                        avg_retrieved_memories=0.5,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=0.0,
                        old_style_only_response_rate=0.75,
                    ),
                    "middle": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=1.5,
                        avg_retrieved_memories=1.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=0.5,
                        old_style_only_response_rate=0.25,
                    ),
                    "late": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=2.0,
                        avg_retrieved_memories=2.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=1.0,
                        old_style_only_response_rate=0.0,
                    ),
                },
                first_record_elapsed_seconds=0.0,
                stabilization_elapsed_seconds=10.0,
            ),
            module.ScenarioReport(
                scenario_tag="scenario-2",
                conversation_id="conv-2",
                passed=True,
                findings=[],
                recommendations=[],
                dialog_step_results=[],
                quality_score=96,
                scenario_counts={"messages": 48, "records": 1, "summaries": 0},
                duplication_report={
                    "duplicate_observation_count": 0,
                    "distillation_merge_count": 0,
                    "stored_distillation_count": 0,
                },
                contamination_report={
                    "long_term_summary_count": 1.0,
                    "contaminated_long_term_summary_count": 0.0,
                    "contaminated_long_term_summary_share": 0.0,
                },
                retrieval_report={
                    "retrieved_memory_hits": 2,
                    "unique_retrieved_memory_summaries": 2,
                    "dominant_summary": "Temporary validation run",
                    "dominant_summary_hits": 1,
                    "dominant_summary_share": 0.5,
                    "late_phase_retrieved_memory_hits": 1,
                    "late_phase_unique_retrieved_memory_summaries": 1,
                    "late_phase_dominant_summary": "Temporary validation run",
                    "late_phase_dominant_summary_hits": 1,
                    "late_phase_dominant_summary_share": 1.0,
                },
                phase_breakdown={
                    "early": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=2.0,
                        avg_retrieved_memories=2.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=0.5,
                        old_style_only_response_rate=0.5,
                    ),
                    "middle": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=3.0,
                        avg_retrieved_memories=3.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=0.75,
                        old_style_only_response_rate=0.0,
                    ),
                    "late": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=4.0,
                        avg_retrieved_memories=4.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=1.0,
                        old_style_only_response_rate=0.0,
                    ),
                },
                first_record_elapsed_seconds=0.0,
                stabilization_elapsed_seconds=60.0,
            ),
            module.ScenarioReport(
                scenario_tag="scenario-3",
                conversation_id="conv-3",
                passed=False,
                findings=[],
                recommendations=[],
                dialog_step_results=[],
                quality_score=80,
                scenario_counts={"messages": 48, "records": 3, "summaries": 1},
                duplication_report={
                    "duplicate_observation_count": 4,
                    "distillation_merge_count": 2,
                    "stored_distillation_count": 1,
                },
                contamination_report={
                    "long_term_summary_count": 2.0,
                    "contaminated_long_term_summary_count": 2.0,
                    "contaminated_long_term_summary_share": 1.0,
                },
                retrieval_report={
                    "retrieved_memory_hits": 8,
                    "unique_retrieved_memory_summaries": 1,
                    "dominant_summary": "Concise preference",
                    "dominant_summary_hits": 8,
                    "dominant_summary_share": 1.0,
                    "late_phase_retrieved_memory_hits": 4,
                    "late_phase_unique_retrieved_memory_summaries": 1,
                    "late_phase_dominant_summary": "Concise preference",
                    "late_phase_dominant_summary_hits": 4,
                    "late_phase_dominant_summary_share": 1.0,
                },
                phase_breakdown={
                    "early": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=3.0,
                        avg_retrieved_memories=4.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=1.0,
                        old_style_only_response_rate=0.0,
                    ),
                    "middle": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=2.0,
                        avg_retrieved_memories=2.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=1.0,
                        old_style_only_response_rate=0.0,
                    ),
                    "late": module.PhaseSummary(
                        dialog_step_count=8,
                        avg_latency_seconds=1.0,
                        avg_retrieved_memories=1.0,
                        avg_retrieved_messages=0.0,
                        current_style_response_rate=1.0,
                        old_style_only_response_rate=0.0,
                    ),
                },
                first_record_elapsed_seconds=12.0,
                stabilization_elapsed_seconds=40.0,
            ),
        ]

        summary = module.summarize_reports(reports)

        assert summary["iterations"] == 3
        assert summary["passed_iterations"] == 2
        assert summary["pass_rate"] == 2 / 3
        assert summary["records"] == {
            "min": 1.0,
            "max": 18.0,
            "avg": 22 / 3,
            "spread": 17.0,
        }
        assert summary["summaries"] == {
            "min": 0.0,
            "max": 3.0,
            "avg": 4 / 3,
            "spread": 3.0,
        }
        assert summary["quality_score"] == {
            "min": 80.0,
            "max": 96.0,
            "avg": 272 / 3,
            "spread": 16.0,
        }
        assert summary["duplicate_observation_count"] == {
            "min": 0.0,
            "max": 4.0,
            "avg": 2.0,
            "spread": 4.0,
        }
        assert summary["distillation_merge_count"] == {
            "min": 0.0,
            "max": 2.0,
            "avg": 1.0,
            "spread": 2.0,
        }
        assert summary["stored_distillation_count"] == {
            "min": 0.0,
            "max": 3.0,
            "avg": 4 / 3,
            "spread": 3.0,
        }
        assert summary["long_term_summary_count"] == {
            "min": 1.0,
            "max": 3.0,
            "avg": 2.0,
            "spread": 2.0,
        }
        assert summary["contaminated_long_term_summary_count"] == {
            "min": 0.0,
            "max": 2.0,
            "avg": 1.0,
            "spread": 2.0,
        }
        assert summary["contaminated_long_term_summary_share"] == {
            "min": 0.0,
            "max": 1.0,
            "avg": 4 / 9,
            "spread": 1.0,
        }
        assert summary["unique_retrieved_memory_summaries"] == {
            "min": 1.0,
            "max": 2.0,
            "avg": 5 / 3,
            "spread": 1.0,
        }
        assert summary["dominant_summary_hits"] == {
            "min": 1.0,
            "max": 8.0,
            "avg": 13 / 3,
            "spread": 7.0,
        }
        assert summary["dominant_summary_share"] == {
            "min": 0.5,
            "max": 1.0,
            "avg": 13 / 18,
            "spread": 0.5,
        }
        assert summary["late_phase_unique_retrieved_memory_summaries"] == {
            "min": 1.0,
            "max": 1.0,
            "avg": 1.0,
            "spread": 0.0,
        }
        assert summary["late_phase_dominant_summary_hits"] == {
            "min": 1.0,
            "max": 4.0,
            "avg": 8 / 3,
            "spread": 3.0,
        }
        assert summary["late_phase_dominant_summary_share"] == {
            "min": 1.0,
            "max": 1.0,
            "avg": 1.0,
            "spread": 0.0,
        }
        assert summary["first_record_elapsed_seconds"] == {
            "min": 0.0,
            "max": 12.0,
            "avg": 4.0,
            "spread": 12.0,
        }
        assert summary["stabilization_elapsed_seconds"] == {
            "min": 10.0,
            "max": 60.0,
            "avg": 110 / 3,
            "spread": 50.0,
        }
        assert summary["phase_breakdown"]["early"]["avg_retrieved_memories"] == {
            "min": 0.5,
            "max": 4.0,
            "avg": 6.5 / 3,
            "spread": 3.5,
        }
        assert summary["phase_breakdown"]["early"]["current_style_response_rate"] == {
            "min": 0.0,
            "max": 1.0,
            "avg": 0.5,
            "spread": 1.0,
        }
        assert summary["phase_breakdown"]["early"]["old_style_only_response_rate"] == {
            "min": 0.0,
            "max": 0.75,
            "avg": 1.25 / 3,
            "spread": 0.75,
        }
        assert summary["phase_breakdown"]["late"]["avg_latency_seconds"] == {
            "min": 1.0,
            "max": 4.0,
            "avg": 7 / 3,
            "spread": 3.0,
        }

    def test_fetch_state_filters_messages_observations_and_reflections(self):
        """Scenario state should keep only graph entries linked to the scenario."""

        class FakeClient:
            """Minimal fake API client for state fetch tests."""

            def __init__(self) -> None:
                self.calls = []

            def get(self, path: str, params=None, timeout=30):
                self.calls.append((path, params))
                if path == "/admin/memory/messages":
                    return {
                        "messages": [
                            {
                                "id": "m1",
                                "conversation_id": "scenario-1",
                            }
                        ]
                    }
                if path == "/admin/memory/records":
                    return {
                        "records": [
                            {
                                "id": "o1",
                                "source_message_ids": ["m1"],
                                "key_facts": ["fact"],
                                "action_items": ["action"],
                            },
                            {
                                "id": "o2",
                                "source_message_ids": ["m-other"],
                                "key_facts": [],
                                "action_items": [],
                            },
                        ]
                    }
                if path == "/admin/memory/summaries":
                    return {
                        "summaries": [
                            {
                                "id": "r1",
                                "source_record_ids": ["o1"],
                                "record_relations": [
                                    {
                                        "record_id": "o1",
                                        "relation": "supports",
                                    }
                                ],
                            },
                            {
                                "id": "r2",
                                "source_record_ids": ["o-other"],
                                "record_relations": [],
                            },
                        ]
                    }
                if path == "/admin/memory/quality":
                    return {"quality_score": 82}
                if path == "/admin/memory/dialog-recorder-trace":
                    return {"entries": []}
                if path == "/admin/memory/distiller-trace":
                    return {"entries": []}
                raise AssertionError(path)

        state = module.fetch_scenario_state(FakeClient(), "scenario-1")

        assert [message["id"] for message in state.messages] == ["m1"]
        assert [record["id"] for record in state.records] == ["o1"]
        assert [summary["id"] for summary in state.summaries] == ["r1"]
        assert state.quality_report["quality_score"] == 82
        assert state.unreflected_record_count == 1
        assert state.scenario_unreflected_record_count == 0
        assert state.covered_message_count == 1
        assert state.message_coverage == 1.0
        assert state.duplication_report == {
            "duplicate_observation_count": 0,
            "distillation_merge_count": 0,
            "stored_distillation_count": 0,
        }
        assert state.contamination_report == {
            "long_term_summary_count": 0.0,
            "contaminated_long_term_summary_count": 0.0,
            "contaminated_long_term_summary_share": 0.0,
        }

    def test_fetch_state_ignores_summary_links_to_missing_observations(self):
        """Global unreflected counts should not go negative on old accumulated data."""

        class FakeClient:
            """Minimal fake API client for accumulated-state count tests."""

            def get(self, path: str, params=None, timeout=30):
                if path == "/admin/memory/messages":
                    return {
                        "messages": [
                            {
                                "id": "m1",
                                "conversation_id": "scenario-1",
                            }
                        ]
                    }
                if path == "/admin/memory/records":
                    return {
                        "records": [
                            {
                                "id": "o1",
                                "source_message_ids": ["m1"],
                                "key_facts": [],
                                "action_items": [],
                            }
                        ]
                    }
                if path == "/admin/memory/summaries":
                    return {
                        "summaries": [
                            {
                                "id": "r1",
                                "source_record_ids": [
                                    "o1",
                                    "o-stale-1",
                                    "o-stale-2",
                                ],
                                "record_relations": [
                                    {
                                        "record_id": "o1",
                                        "relation": "supports",
                                    }
                                ],
                            }
                        ]
                    }
                if path == "/admin/memory/quality":
                    return {"quality_score": 82}
                if path == "/admin/memory/dialog-recorder-trace":
                    return {"entries": []}
                if path == "/admin/memory/distiller-trace":
                    return {"entries": []}
                raise AssertionError(path)

        state = module.fetch_scenario_state(FakeClient(), "scenario-1")

        assert state.unreflected_record_count == 0
        assert state.scenario_unreflected_record_count == 0

    def test_evaluate_scenario_reports_missing_reflections(self):
        """Evaluation should recommend distiller work when no summaries exist."""
        state = module.ScenarioState(
            conversation_id="scenario-1",
            messages=[{"id": "m1"}, {"id": "m2"}],
            records=[
                {
                    "id": "o1",
                    "source_message_ids": ["m1"],
                    "key_facts": ["fact"],
                    "action_items": ["action"],
                }
            ],
            summaries=[],
            quality_report={
                "quality_score": 84,
            },
            unreflected_record_count=module.DISTILLATION_TRIGGER_THRESHOLD,
            scenario_unreflected_record_count=module.DISTILLATION_TRIGGER_THRESHOLD,
        )
        dialog_step_results = [
            module.ChatDialogStepResult(
                prompt="prompt-1",
                response_text="You currently prefer detailed answers.",
                elapsed_seconds=1.0,
                retrieved_memories=[],
                retrieved_messages=[],
            )
        ]

        report = module.evaluate_scenario(
            scenario_tag="scenario-tag",
            conversation_id="scenario-1",
            state=state,
            dialog_step_results=dialog_step_results,
        )

        assert not report.passed
        assert any(
            "Lower the distiller threshold" in recommendation
            for recommendation in report.recommendations
        )

    def test_evaluate_scenario_skips_summary_failure_below_threshold(self):
        """Summary checks should be skipped before the global threshold."""
        state = module.ScenarioState(
            conversation_id="scenario-1",
            messages=[{"id": "m1"}, {"id": "m2"}],
            records=[
                {
                    "id": "o1",
                    "source_message_ids": ["m1"],
                    "key_facts": ["fact"],
                    "action_items": ["action"],
                }
            ],
            summaries=[],
            quality_report={
                "quality_score": 84,
            },
            unreflected_record_count=4,
            scenario_unreflected_record_count=1,
        )
        dialog_step_results = [
            module.ChatDialogStepResult(
                prompt="prompt-1",
                response_text="You currently prefer detailed answers.",
                elapsed_seconds=1.0,
                retrieved_memories=[],
                retrieved_messages=[],
            )
        ]

        report = module.evaluate_scenario(
            scenario_tag="scenario-tag",
            conversation_id="scenario-1",
            state=state,
            dialog_step_results=dialog_step_results,
        )

        assert not any(
            finding.name == "Distiller produced scenario summaries"
            and not finding.passed
            for finding in report.findings
        )
        assert not any(
            "Lower the distiller threshold" in recommendation
            for recommendation in report.recommendations
        )

    def test_build_phase_breakdown_tracks_early_middle_and_late_behavior(self):
        """Phase diagnostics should expose timing and retrieval drift by thirds."""
        dialog_step_results = [
            module.ChatDialogStepResult(
                prompt=f"prompt-{index}",
                response_text=response_text,
                elapsed_seconds=float(index),
                retrieved_memories=[{"summary": "memory"}] * retrieved_memory_count,
                retrieved_messages=[{"id": "m"}] * retrieved_message_count,
                error=error_text,
            )
            for (
                index,
                response_text,
                retrieved_memory_count,
                retrieved_message_count,
                error_text,
            ) in [
                (1, "You prefer concise answers.", 0, 0, None),
                (2, "You prefer concise answers.", 1, 0, None),
                (3, "You now prefer detailed step-by-step answers.", 2, 1, None),
                (4, "You now prefer detailed step-by-step answers.", 3, 1, None),
                (5, "You now prefer detailed step-by-step answers.", 4, 2, None),
                (6, "", 5, 2, "boom"),
            ]
        ]

        phase_breakdown = module.build_phase_breakdown(dialog_step_results)

        assert phase_breakdown["early"].dialog_step_count == 2
        assert phase_breakdown["early"].avg_latency_seconds == 1.5
        assert phase_breakdown["early"].avg_retrieved_memories == 0.5
        assert phase_breakdown["early"].current_style_response_rate == 0.0
        assert phase_breakdown["early"].old_style_only_response_rate == 1.0
        assert phase_breakdown["middle"].dialog_step_count == 2
        assert phase_breakdown["middle"].avg_retrieved_messages == 1.0
        assert phase_breakdown["middle"].current_style_response_rate == 1.0
        assert phase_breakdown["late"].dialog_step_count == 2
        assert phase_breakdown["late"].avg_retrieved_memories == 4.5
        assert phase_breakdown["late"].error_count == 1

    def test_evaluate_scenario_flags_old_preference_recall(self):
        """Evaluation should warn when final recall still prefers the old style."""
        state = module.ScenarioState(
            conversation_id="scenario-1",
            messages=[{"id": "m1"}, {"id": "m2"}],
            records=[
                {
                    "id": "o1",
                    "source_message_ids": ["m1"],
                    "key_facts": ["fact"],
                    "action_items": ["action"],
                }
            ],
            summaries=[
                {
                    "id": "r1",
                    "source_record_ids": ["o1"],
                    "record_relations": [
                        {
                            "record_id": "o1",
                            "relation": "supports",
                        }
                    ],
                }
            ],
            quality_report={"quality_score": 84},
            unreflected_record_count=4,
            scenario_unreflected_record_count=0,
        )
        dialog_step_results = [
            module.ChatDialogStepResult(
                prompt="prompt-1",
                response_text="You currently prefer concise and short answers.",
                elapsed_seconds=1.0,
                retrieved_memories=[],
                retrieved_messages=[],
            )
        ]

        report = module.evaluate_scenario(
            scenario_tag="scenario-tag",
            conversation_id="scenario-1",
            state=state,
            dialog_step_results=dialog_step_results,
        )

        assert any(
            finding.name == "Final long-term recall is not stuck on the old preference"
            and not finding.passed
            for finding in report.findings
        )
        assert any(
            finding.name == "Final long-term recall keeps the new preference active"
            and not finding.passed
            for finding in report.findings
        )

    def test_evaluate_scenario_accepts_fast_post_chat_observation(self):
        """Evaluation should pass when the first post-chat record is timely."""
        state = module.ScenarioState(
            conversation_id="scenario-1",
            messages=[{"id": "m1"}, {"id": "m2"}],
            records=[
                {
                    "id": "o1",
                    "source_message_ids": ["m1", "m2"],
                    "key_facts": ["fact"],
                    "action_items": ["action"],
                }
            ],
            summaries=[],
            quality_report={"quality_score": 84},
            unreflected_record_count=1,
            scenario_unreflected_record_count=1,
            covered_message_count=2,
            message_coverage=1.0,
            first_record_elapsed_seconds=12.0,
            stabilization_elapsed_seconds=20.0,
        )
        dialog_step_results = [
            module.ChatDialogStepResult(
                prompt="prompt-1",
                response_text="You currently prefer detailed answers.",
                elapsed_seconds=1.0,
                retrieved_memories=[],
                retrieved_messages=[],
            )
        ]

        report = module.evaluate_scenario(
            scenario_tag="scenario-tag",
            conversation_id="scenario-1",
            state=state,
            dialog_step_results=dialog_step_results,
        )

        assert any(
            finding.name == "Dialog Recorder catches up during reader pause"
            and finding.passed
            for finding in report.findings
        )

    def test_evaluate_scenario_accepts_structured_long_term_heading(self):
        """Structured final recalls should count when content follows the heading."""
        state = module.ScenarioState(
            conversation_id="scenario-1",
            messages=[{"id": "m1"}, {"id": "m2"}],
            records=[
                {
                    "id": "o1",
                    "source_message_ids": ["m1", "m2"],
                    "key_facts": ["fact"],
                    "action_items": ["action"],
                }
            ],
            summaries=[
                {
                    "id": "r1",
                    "source_record_ids": ["o1"],
                    "record_relations": [
                        {
                            "record_id": "o1",
                            "relation": "supports",
                        }
                    ],
                }
            ],
            quality_report={"quality_score": 84},
            unreflected_record_count=0,
            scenario_unreflected_record_count=0,
        )
        dialog_step_results = [
            module.ChatDialogStepResult(
                prompt="prompt-1",
                response_text=(
                    "Final active long-term preference:\n\n"
                    "You prefer clear, detailed, step-by-step explanations "
                    "with full context and reasoning.\n\n"
                    "Temporary short-term context:\n\n"
                    "This session is a temporary validation run."
                ),
                elapsed_seconds=1.0,
                retrieved_memories=[],
                retrieved_messages=[],
            )
        ]

        report = module.evaluate_scenario(
            scenario_tag="scenario-tag",
            conversation_id="scenario-1",
            state=state,
            dialog_step_results=dialog_step_results,
        )

        assert any(
            finding.name == "Final long-term recall keeps the new preference active"
            and finding.passed
            for finding in report.findings
        )


class TestScenarioWait:
    """Verify scenario wait logic respects summary thresholds."""

    def test_wait_for_scenario_memory_waits_for_summary_after_threshold(
        self,
        monkeypatch,
    ):
        """Threshold-reached scenarios should wait until a summary appears."""
        states = iter(
            [
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=module.DISTILLATION_TRIGGER_THRESHOLD,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=module.DISTILLATION_TRIGGER_THRESHOLD,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[{"id": "r1"}],
                    quality_report={},
                    unreflected_record_count=module.DISTILLATION_TRIGGER_THRESHOLD,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[{"id": "r1"}],
                    quality_report={},
                    unreflected_record_count=module.DISTILLATION_TRIGGER_THRESHOLD,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[{"id": "r1"}],
                    quality_report={},
                    unreflected_record_count=module.DISTILLATION_TRIGGER_THRESHOLD,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
            ]
        )
        monotonic_values = count()

        monkeypatch.setattr(module, "fetch_scenario_state", lambda *_args: next(states))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)
        monkeypatch.setattr(
            module.time,
            "monotonic",
            lambda: next(monotonic_values),
        )

        state = module.wait_for_scenario_memory(
            client=object(),
            conversation_id="scenario-1",
            expected_dialog_step_count=14,
            timeout_seconds=10,
            poll_interval_seconds=1,
        )

        assert len(state.summaries) == 1

    def test_wait_for_scenario_memory_returns_without_summary_below_threshold(
        self,
        monkeypatch,
    ):
        """Below the summary threshold, records alone are enough to settle."""
        states = iter(
            [
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=4,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=4,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=4,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
            ]
        )
        monotonic_values = count()

        monkeypatch.setattr(module, "fetch_scenario_state", lambda *_args: next(states))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)
        monkeypatch.setattr(
            module.time,
            "monotonic",
            lambda: next(monotonic_values),
        )

        state = module.wait_for_scenario_memory(
            client=object(),
            conversation_id="scenario-1",
            expected_dialog_step_count=14,
            timeout_seconds=10,
            poll_interval_seconds=1,
        )

        assert len(state.summaries) == 0

    def test_wait_for_scenario_memory_ignores_global_summary_backlog(
        self,
        monkeypatch,
    ):
        """Scenario settling should ignore unrelated global summary backlog."""
        states = iter(
            [
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=30,
                    scenario_unreflected_record_count=4,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=30,
                    scenario_unreflected_record_count=4,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 28,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=30,
                    scenario_unreflected_record_count=4,
                    covered_message_count=28,
                    message_coverage=1.0,
                ),
            ]
        )
        monotonic_values = count()

        monkeypatch.setattr(module, "fetch_scenario_state", lambda *_args: next(states))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)
        monkeypatch.setattr(
            module.time,
            "monotonic",
            lambda: next(monotonic_values),
        )

        state = module.wait_for_scenario_memory(
            client=object(),
            conversation_id="scenario-1",
            expected_dialog_step_count=14,
            timeout_seconds=10,
            poll_interval_seconds=1,
        )

        assert len(state.summaries) == 0

    def test_wait_for_scenario_memory_respects_required_record_count(
        self,
        monkeypatch,
    ):
        """Wait logic should not settle before the required record count."""
        states = iter(
            [
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=1,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=1,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=2,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=2,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=2,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
            ]
        )
        monotonic_values = count()

        monkeypatch.setattr(module, "fetch_scenario_state", lambda *_args: next(states))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)
        monkeypatch.setattr(
            module.time,
            "monotonic",
            lambda: next(monotonic_values),
        )

        state = module.wait_for_scenario_memory(
            client=object(),
            conversation_id="scenario-1",
            expected_dialog_step_count=24,
            timeout_seconds=10,
            poll_interval_seconds=1,
            required_record_count=2,
        )

        assert len(state.records) == 2

    def test_wait_for_scenario_memory_respects_required_message_coverage(
        self,
        monkeypatch,
    ):
        """Wait logic should not settle before message coverage is high enough."""
        states = iter(
            [
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}, {"id": "o3"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=3,
                    covered_message_count=20,
                    message_coverage=0.41,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}, {"id": "o3"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=3,
                    covered_message_count=20,
                    message_coverage=0.41,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}, {"id": "o3"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=3,
                    covered_message_count=30,
                    message_coverage=0.63,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}, {"id": "o3"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=3,
                    covered_message_count=30,
                    message_coverage=0.63,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}, {"id": "o2"}, {"id": "o3"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=3,
                    covered_message_count=30,
                    message_coverage=0.63,
                ),
            ]
        )
        monotonic_values = count()

        monkeypatch.setattr(module, "fetch_scenario_state", lambda *_args: next(states))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)
        monkeypatch.setattr(
            module.time,
            "monotonic",
            lambda: next(monotonic_values),
        )

        state = module.wait_for_scenario_memory(
            client=object(),
            conversation_id="scenario-1",
            expected_dialog_step_count=24,
            timeout_seconds=10,
            poll_interval_seconds=1,
            required_record_count=3,
            required_message_coverage=0.6,
        )

        assert state.covered_message_count == 30
        assert state.message_coverage == 0.63

    def test_wait_for_scenario_memory_accepts_single_full_coverage_observation(
        self,
        monkeypatch,
    ):
        """One aggregated record should settle when it covers all messages."""
        states = iter(
            [
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=1,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=1,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 48,
                    records=[{"id": "o1"}],
                    summaries=[],
                    quality_report={},
                    unreflected_record_count=1,
                    covered_message_count=48,
                    message_coverage=1.0,
                ),
            ]
        )
        monotonic_values = count()

        monkeypatch.setattr(module, "fetch_scenario_state", lambda *_args: next(states))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)
        monkeypatch.setattr(
            module.time,
            "monotonic",
            lambda: next(monotonic_values),
        )

        state = module.wait_for_scenario_memory(
            client=object(),
            conversation_id="scenario-1",
            expected_dialog_step_count=24,
            timeout_seconds=10,
            poll_interval_seconds=1,
            required_record_count=6,
        )

        assert len(state.records) == 1
        assert state.message_coverage == 1.0
        assert state.first_record_elapsed_seconds == 2.0

    def test_wait_for_scenario_memory_returns_stable_low_coverage_state(
        self,
        monkeypatch,
    ):
        """Stable low coverage should finish early and fail later in findings."""
        states = iter(
            [
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 200,
                    records=[{"id": "o1"}] * 53,
                    summaries=[{"id": "r1"}] * 8,
                    quality_report={},
                    unreflected_record_count=0,
                    scenario_unreflected_record_count=0,
                    covered_message_count=106,
                    message_coverage=0.53,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 200,
                    records=[{"id": "o1"}] * 53,
                    summaries=[{"id": "r1"}] * 8,
                    quality_report={},
                    unreflected_record_count=0,
                    scenario_unreflected_record_count=0,
                    covered_message_count=106,
                    message_coverage=0.53,
                ),
                module.ScenarioState(
                    conversation_id="scenario-1",
                    messages=[{}] * 200,
                    records=[{"id": "o1"}] * 53,
                    summaries=[{"id": "r1"}] * 8,
                    quality_report={},
                    unreflected_record_count=0,
                    scenario_unreflected_record_count=0,
                    covered_message_count=106,
                    message_coverage=0.53,
                ),
            ]
        )
        monotonic_values = count()

        monkeypatch.setattr(module, "fetch_scenario_state", lambda *_args: next(states))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)
        monkeypatch.setattr(
            module.time,
            "monotonic",
            lambda: next(monotonic_values),
        )

        state = module.wait_for_scenario_memory(
            client=object(),
            conversation_id="scenario-1",
            expected_dialog_step_count=100,
            timeout_seconds=600,
            poll_interval_seconds=5,
            required_record_count=1,
            required_message_coverage=0.6,
        )

        assert state.message_coverage == 0.53
        assert state.covered_message_count == 106
        assert state.stabilization_elapsed_seconds == 6.0
