"""Unit tests for the memory loop script: scenario filtering.

Covers conversation collection, legacy-conversation cleanup, and
scenario prompt building in the memory loop evaluation script.
"""

from __future__ import annotations

import argparse
import sys
from itertools import count
from urllib.error import HTTPError

from .memory_loop_fakes import _empty_phase_breakdown, module


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
