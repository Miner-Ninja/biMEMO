"""Unit tests for the memory loop script: scenario waiting.

Covers the memory-processing wait loop and the dialog-step sending
behavior of the memory loop evaluation script.
"""

from __future__ import annotations

from itertools import count

from .memory_loop_fakes import module


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
