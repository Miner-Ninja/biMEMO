"""Constants for the memory loop evaluation harness."""

from __future__ import annotations


DEFAULT_BASE_URL = "http://127.0.0.1:5273"
CONTAINER_LOCAL_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_CHAT_TIMEOUT_SECONDS = 300
DEFAULT_GET_TIMEOUT_SECONDS = 30
DEFAULT_PIPELINE_TIMEOUT_SECONDS = 900
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_MAX_ITERATIONS = 1
DEFAULT_CHAT_DELAY_SECONDS = 3.0
PAGE_SIZE = 200
DISTILLATION_TRIGGER_THRESHOLD = 20
SCENARIO_DISTILLATION_BUFFER = 4
PHASE_COUNT = 3
PHASE_NAMES = ("early", "middle", "late")
CHAT_RETRY_LIMIT = 3
REQUEST_RETRY_LIMIT = 6
RATE_LIMIT_BACKOFF_SECONDS = 15.0
MIN_OBSERVATION_PROVENANCE_COVERAGE = 0.8
MIN_STRUCTURED_OBSERVATION_COVERAGE = 0.5
MIN_GLOBAL_QUALITY_SCORE = 70
MIN_RELATION_VARIETY_FOR_SHIFT = 1
EXPECTED_MESSAGE_MULTIPLIER = 2
EXPECTED_DIALOG_STEPS_PER_OBSERVATION = 4
MIN_SCENARIO_MESSAGE_COVERAGE = 0.6
FULL_SCENARIO_MESSAGE_COVERAGE = 1.0
MAX_POST_CHAT_FIRST_OBSERVATION_SECONDS = 45.0
VALID_RELATION_LABELS = {"supports", "conflicts", "outdates"}
CURRENT_PREFERENCE_MARKERS = ("detailed", "step-by-step", "richer")
OLD_PREFERENCE_MARKERS = ("concise", "short", "brief")
LONG_TERM_SCOPE_MARKERS = (
    "long-term",
    "long term",
    "default",
    "active preference",
    "current style",
    "future technical",
    "future debugging",
)
OLD_STYLE_NEGATION_MARKERS = (
    "no longer",
    "not ",
    "instead",
    "replace",
    "replaced",
    "outdated",
    "former",
    "previous",
    "earlier",
)
TEMPORARY_SCOPE_MARKERS = ("short-term", "short term", "temporary")
MEMORY_LOOP_CONVERSATION_PREFIX = "memory-loop-"
AUTH_EXEMPT_PATHS = {
    "/auth/login",
    "/auth/setup",
    "/auth/status",
    "/csrf-token",
}
DEFAULT_SCENARIO_PROFILE = "control"
