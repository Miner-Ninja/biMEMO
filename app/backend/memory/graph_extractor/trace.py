"""JSONL decision trace for the graph extractor.

One line per decision, written best-effort to the runtime directory
(``BIMEMO_RUNTIME_DIR``, default ``.runtime``). The file mechanics live
in the shared JSONL trace; this module keeps the entry shape (millisecond
timestamp + decision + detail fields) and the decision vocabulary.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from backend.constants import ENV_RUNTIME_DIR
from backend.memory.trace import JsonlTrace


logger = logging.getLogger(__name__)

TRACE_FILE_NAME = "graph_extractor_trace.jsonl"

TRACE_DECISION_STORED = "assertion_stored"
TRACE_DECISION_DUPLICATE = "assertion_skipped_duplicate"
TRACE_DECISION_INVALIDATED = "assertion_invalidated_previous"
TRACE_DECISION_CONFLICT_KEPT = "assertion_conflict_kept_both"
TRACE_DECISION_SUBJECT_UNDECLARED = "assertion_skipped_subject_undeclared"
TRACE_DECISION_FORBIDDEN_ENTITY = "entity_skipped_forbidden_name"
TRACE_DECISION_PARSE_EMPTY = "extraction_empty_or_unparsed"
TRACE_DECISION_UNSAFE_OUTPUT = "assertion_skipped_unsafe_output"


def _trace_path() -> str:
    """Return the trace file path inside the runtime directory."""
    runtime_dir = os.environ.get(ENV_RUNTIME_DIR, ".runtime")
    return os.path.join(runtime_dir, TRACE_FILE_NAME)


_trace = JsonlTrace(_trace_path, label="graph extractor")


def write_graph_extractor_trace(
    decision: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one decision line to the extractor trace (best-effort)."""
    entry = {
        "timestamp": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
        "decision": decision,
        **(detail or {}),
    }
    _trace.append(entry)


def read_graph_extractor_trace(
    limit: int = 100,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent extractor trace entries, newest-first.

    Best-effort read of the local diagnostics JSONL file. A missing or
    unreadable file yields an empty list — the trace is diagnostic only.
    """
    return _trace.read(limit=limit, decision=decision)
