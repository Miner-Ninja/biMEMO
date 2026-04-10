"""Public classification surface for memory-related questions."""

from backend.memory.classification.fallback_classifier import (
    SemanticFallbackResult,
    build_fallback_classifier_messages,
    classify_memory_question_with_fallback,
    classify_memory_question_with_llm_call,
    parse_fallback_classification,
)
from backend.memory.classification.fallback_policy import (
    CONFLICTING_SIGNALS_REASON,
    UNSUPPORTED_LANGUAGE_QUESTION_REASON,
    looks_like_unknown_language_question,
    semantic_fallback_reason,
    should_use_semantic_fallback,
)
from backend.memory.classification.models import (
    ClassificationConfidence,
    MemoryQuestionClassification,
    MemoryQuestionIntent,
)
from backend.memory.classification.question_classifier import (
    classify_memory_question,
    detect_message_role_filter,
    is_memory_query,
    is_persistence_question,
)
from backend.memory.classification.question_router import (
    MemoryQuestionRoute,
    route_memory_question,
)
from backend.memory.classification.resolver import (
    LongTermMemoryResolver,
)
from backend.memory.classification.trace import (
    MemoryQuestionRouteTrace,
)


__all__ = [
    "CONFLICTING_SIGNALS_REASON",
    "UNSUPPORTED_LANGUAGE_QUESTION_REASON",
    "ClassificationConfidence",
    "LongTermMemoryResolver",
    "MemoryQuestionClassification",
    "MemoryQuestionIntent",
    "MemoryQuestionRoute",
    "MemoryQuestionRouteTrace",
    "SemanticFallbackResult",
    "build_fallback_classifier_messages",
    "classify_memory_question",
    "classify_memory_question_with_fallback",
    "classify_memory_question_with_llm_call",
    "detect_message_role_filter",
    "is_memory_query",
    "is_persistence_question",
    "looks_like_unknown_language_question",
    "parse_fallback_classification",
    "route_memory_question",
    "semantic_fallback_reason",
    "should_use_semantic_fallback",
]
