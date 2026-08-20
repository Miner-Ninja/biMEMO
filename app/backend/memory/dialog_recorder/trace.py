"""File-based dialog recorder pipeline trace for diagnostics.

Public entry points over the shared JSONL trace: the file path stays
module-level (tests repoint it via monkeypatch) and entries get a
stable second-precision UTC timestamp when the caller did not set one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.memory.trace import JsonlTrace


logger = logging.getLogger(__name__)

TRACE_FILE_PATH = (
    Path(__file__).resolve().parents[3] / ".runtime" / "dialog_recorder_trace.jsonl"
)

_trace = JsonlTrace(lambda: TRACE_FILE_PATH, label="dialog recorder")


def _normalize_trace_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a trace entry with a stable timestamp field."""
    normalized = dict(entry)
    normalized.setdefault(
        "timestamp",
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    return normalized


def append_dialog_recorder_trace(entry: dict[str, Any]) -> None:
    """Append one dialog recorder trace entry to the JSONL file."""
    _trace.append(_normalize_trace_entry(entry))


def read_dialog_recorder_trace(
    limit: int = 100,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent dialog recorder trace entries in newest-first order."""
    return _trace.read(limit=limit, decision=decision)
