"""Graph extractor: turns dialog records into entities and assertions.

The memory extraction pipeline: dialog records become entities and
assertions. Writes go through the stage-1 graph operations on Socket A.
"""

from .parsing import (
    ExtractedAssertion,
    ExtractedEntity,
    GraphExtraction,
    parse_graph_extraction,
)
from .rules import (
    CONFLICT_INVALIDATE,
    CONFLICT_KEEP_BOTH,
    CONFLICT_SKIP_DUPLICATE,
    normalize_relation,
    resolve_conflict_action,
)
from .runtime import process_graph_extraction


__all__ = [
    "CONFLICT_INVALIDATE",
    "CONFLICT_KEEP_BOTH",
    "CONFLICT_SKIP_DUPLICATE",
    "ExtractedAssertion",
    "ExtractedEntity",
    "GraphExtraction",
    "normalize_relation",
    "parse_graph_extraction",
    "process_graph_extraction",
    "resolve_conflict_action",
]
