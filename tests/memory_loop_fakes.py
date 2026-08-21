"""Support for the memory-loop script unit tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


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
