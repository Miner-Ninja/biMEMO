"""Public surface for context assembly."""

from backend.memory.context_builder.models import ContextEntry, ContextResult
from backend.memory.context_builder.runtime import (
    build_context_with_ids,
    compute_memory_budget,
    estimate_tokens,
    should_compress,
)


__all__ = [
    "ContextEntry",
    "ContextResult",
    "build_context_with_ids",
    "compute_memory_budget",
    "estimate_tokens",
    "should_compress",
]
