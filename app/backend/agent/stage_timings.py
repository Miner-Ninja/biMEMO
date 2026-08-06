"""Per-turn accumulator of named processing-phase wall-clock spans.

One assistant turn passes through several phases — memory recall, context
assembly, and any tool calls — before the model reads the prompt and generates
the answer. This collector records the wall-clock duration of each phase as it
happens, in execution order. It is read once at finalize time to build the
persisted per-stage timing breakdown (see ``backend.llm.usage``).
"""

from __future__ import annotations


def humanize_stage_label(tool_name: str) -> str:
    """Turn a raw tool name into a display label (``web_search`` -> ``Web search``)."""
    return tool_name.replace("_", " ").strip().capitalize() or tool_name


class StageTimings:
    """Ordered ``(label, duration_ms)`` spans recorded during one turn."""

    def __init__(self) -> None:
        """Start an empty per-turn accumulator."""
        self._phases: list[tuple[str, float]] = []

    def record(self, label: str, duration_ms: float) -> None:
        """Append one completed phase span."""
        self._phases.append((label, duration_ms))

    @property
    def phases(self) -> list[tuple[str, float]]:
        """Ordered ``(label, duration_ms)`` spans recorded so far."""
        return list(self._phases)
