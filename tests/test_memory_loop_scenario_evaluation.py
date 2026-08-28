"""Unit tests for the memory loop script: scenario evaluation.

Covers the per-iteration run flow, scenario state fetching, report
assembly, and the improvement recommendations of the memory loop
evaluation script.
"""

from __future__ import annotations

from .memory_loop_fakes import module


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
                if path == "/admin/memory/record-processing-diagnostics":
                    return {"entries": []}
                if path == "/admin/memory/summary-processing-diagnostics":
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
                if path == "/admin/memory/record-processing-diagnostics":
                    return {"entries": []}
                if path == "/admin/memory/summary-processing-diagnostics":
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
