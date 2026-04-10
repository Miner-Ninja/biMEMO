"""Internal data models for the four-tier context assembly pipeline.

Defines the rolling assembly state and prompt entry result structures
used during context building. These are internal to the context builder
and not part of the public API exposed via models.py. Consumed by
runtime.py and runtime_helpers.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.memory.context_builder.models import ContextEntry


COMPRESSED_MEMORY_GUIDANCE = (
    "Compressed memory guidance:\n"
    "- Some older context below is compressed into records and summaries.\n"
    "- Treat that compressed memory as a durable map of past context, not as a "
    "word-for-word transcript.\n"
    "- If the user asks for exact wording, exact dates, exact commands, or proof, "
    "prefer stronger evidence from retrieved messages when available.\n"
    "- If compressed memory and source messages disagree, trust source messages "
    "over records, and records over summaries."
)


@dataclass(slots=True)
class ContextAssemblyState:
    """Rolling state while assembling the four context tiers."""

    entries: list[ContextEntry] = field(default_factory=list)
    used_tokens: int = 0
    retrieved_memories: list[dict[str, str]] = field(default_factory=list)
    retrieved_msg_debug: list[dict[str, str]] = field(default_factory=list)
    retrieved_summary_debug: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PromptEntryBuildResult:
    """Labeled prompt entry plus the metadata needed by the caller."""

    content: str
    cost: int
    is_matched: bool
    text: str
    date_str: str
    identifier: str = ""
