"""Constants used by the summary distiller pipeline."""

import re


DISTILLER_MAX_TOKENS = 220
DISTILLER_TEMPERATURE = 0.0
DISTILLER_SYSTEM_PROMPT = (
    "You are a synthesis agent. Given a list of records, "
    "identify a main point and supporting reasons.\n"
    "Rules:\n"
    "- main_point: identified main conclusion or recurring theme "
    "(max 500 chars)\n"
    "- based_on: supporting reasons drawn from the records "
    "(max 10 items)\n"
    "- trust_level: how much you trust the conclusion "
    "(high/medium/low)\n"
    "- scope: long_term for durable user preferences, stable working style, "
    "or persistent project knowledge; short_term for current test runs, "
    "active temporary tasks, or session-specific context\n"
    "- Choose short_term when the records mainly describe an active test, "
    "temporary task, or current-session work that should not shape future "
    "answers after the task ends\n"
    "- Choose long_term when the records mainly describe durable answer "
    "preferences, recurring user habits, or stable project knowledge that "
    "should guide future answers\n"
    "- If the records show a meaningful change over time, "
    "describe the old state and the new state explicitly in main_point\n"
    "- When a newer preference or fact replaces an older one, include at least "
    "one based_on reason that explains why the newer state overrides the older "
    "state\n"
    "- Output ONLY this plain text format, no JSON and no extra commentary:\n"
    "MAIN_POINT: <text>\n"
    "TRUST_LEVEL: <high|medium|low>\n"
    "SCOPE: <long_term|short_term>\n"
    "BASED_ON:\n"
    "- <reason 1>\n"
    "- <reason 2>\n"
    "- Write main_point and based_on in the same primary language as "
    "the records\n"
    "- Do not translate Russian records into English\n"
    "/no_think"
)
MAX_FILTERED_BASED_ON_ITEMS = 10
DISTILLER_TRACE_DECISIONS = {
    "stored": "summary_stored_by_llm_synthesis",
    "no_observations": "summary_skipped_by_no_eligible_observations",
    "parse_failed": "summary_skipped_by_llm_output_parse_failure",
    "llm_error": "summary_skipped_by_llm_error",
    "unsafe_output": "summary_rejected_by_output_filter",
    "duplicate_distillation": "summary_rejected_by_duplicate_check",
    "merged_into_existing": "summary_merged_by_existing_main_point_match",
    "overreaching_short_term": "summary_rejected_by_short_term_overreach",
    "overreaching_reversion": "summary_rejected_by_preference_reversion_overreach",
}
TRACE_DECISION_STORED = DISTILLER_TRACE_DECISIONS["stored"]
TRACE_DECISION_NO_OBSERVATIONS = DISTILLER_TRACE_DECISIONS["no_observations"]
TRACE_DECISION_PARSE_FAILED = DISTILLER_TRACE_DECISIONS["parse_failed"]
TRACE_DECISION_LLM_ERROR = DISTILLER_TRACE_DECISIONS["llm_error"]
TRACE_DECISION_UNSAFE_OUTPUT = DISTILLER_TRACE_DECISIONS["unsafe_output"]
TRACE_DECISION_DUPLICATE_DISTILLATION = DISTILLER_TRACE_DECISIONS[
    "duplicate_distillation"
]
TRACE_DECISION_MERGED_INTO_EXISTING = DISTILLER_TRACE_DECISIONS["merged_into_existing"]
TRACE_DECISION_OVERREACHING_SHORT_TERM = DISTILLER_TRACE_DECISIONS[
    "overreaching_short_term"
]
TRACE_DECISION_OVERREACHING_REVERSION = DISTILLER_TRACE_DECISIONS[
    "overreaching_reversion"
]
TRACE_SCOPE_ADJUSTMENT_BY_TIME_SIGNALS_AND_LLM_MISMATCH = (
    "scope_downgraded_by_time_signals_and_llm_mismatch"
)
DISTILLER_OUTPUT_FORMAT = {
    "main_point_prefix": "MAIN_POINT:",
    "trust_level_prefix": "TRUST_LEVEL:",
    "scope_prefix": "SCOPE:",
    "based_on_header": "BASED_ON:",
    "list_item_prefix": "- ",
}
DISTILLER_MAIN_POINT_PREFIX = DISTILLER_OUTPUT_FORMAT["main_point_prefix"]
DISTILLER_TRUST_LEVEL_PREFIX = DISTILLER_OUTPUT_FORMAT["trust_level_prefix"]
DISTILLER_SCOPE_PREFIX = DISTILLER_OUTPUT_FORMAT["scope_prefix"]
DISTILLER_BASED_ON_HEADER = DISTILLER_OUTPUT_FORMAT["based_on_header"]
DISTILLER_LIST_ITEM_PREFIX = DISTILLER_OUTPUT_FORMAT["list_item_prefix"]
RECENT_DISTILLATION_DEDUP_LIMIT = 12
MIN_DUPLICATE_DISTILLATION_TOKEN_OVERLAP = 0.7
MIN_DUPLICATE_BASED_ON_TOKEN_OVERLAP = 0.8
MIN_BASED_ON_TOKEN_LENGTH = 3
BASED_ON_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
SHORT_TERM_DISTILLATION_MARKERS = (
    "memory-loop-",
    "this memory test",
    "current test run",
    "test identifier",
    "validating the bimemo memory subsystems",
    "validating bimemo's memory subsystems",
    "active validation run",
    "current session",
)
SHORT_TERM_OBSERVATION_MARKERS = (
    "active task",
    "current task",
    "temporary task",
    "test run",
    "validation run",
    "current session",
    "this session",
    "for now",
    "right now",
    "currently",
    "сейчас",
    "текущ",
    "в этом тест",
    "в рамках тест",
)
DETAILED_PREFERENCE_MARKERS = (
    "detailed",
    "step-by-step",
    "step by step",
    "context",
    "reasoning",
    "подроб",
    "пошаг",
    "контекст",
    "обосн",
    "рассужд",
)
CONCISE_PREFERENCE_MARKERS = (
    "concise",
    "brief",
    "short",
    "direct",
    "кратк",
    "коротк",
    "прям",
)
NEGATED_CONCISE_MARKERS = (
    "no longer",
    "not anymore",
    "changed from concise",
    "instead of concise",
    "больше не",
    "уже не",
    "вместо крат",
    "переш",
)
NEGATED_DETAILED_MARKERS = (
    "no longer detailed",
    "not detailed anymore",
    "changed from detailed",
    "instead of detailed",
    "больше не подроб",
    "уже не подроб",
    "вместо подроб",
)
DEBUGGING_MARKERS = (
    "debug",
    "debugging",
    "отлад",
)
FUTURE_SCOPE_MARKERS = (
    "future",
    "all future",
    "for all future",
    "будущ",
    "все будущ",
)
NO_DURABLE_PREFERENCE_MARKERS = (
    "no durable preference",
    "no long-term preference",
    "nothing from this session should be remembered as long-term",
    "nothing from this session should be remembered long-term",
    "will not persist beyond this run",
    "will not carry over to future chats",
    "will be discarded when the run ends",
    "no preferences from this session matter after it is over",
    "none of today's preferences will persist beyond this session",
)
TEMPORARY_PREFERENCE_MARKERS = (
    "temporary",
    "temporary style",
    "temporary answer style",
    "only for this session",
    "only for the current session",
    "exclusive to the active session",
    "not the default",
    "strictly temporary",
)
CONCISE_BASELINE_MARKERS = (
    "original preference",
    "baseline preference",
    "baseline that should apply",
    "remains the default",
    "default for all future sessions",
    "future sessions default",
    "revert to the original concise preference",
    "return to the original concise preference",
    "brief, direct, and concise technical answers remains the default",
)
SEPARATION_MARKERS = (
    "should not outweigh my long-term answer-style preference",
    "should stay separate from my durable answer-style preference",
    "what part of this conversation should remain durable preference memory",
    "what should remain long-term for future conversations",
    "what answer style should you still remember",
    "which preference should still matter after the validation run is over",
    "what should you remember about my current answer style in future replies",
    "state the final active long-term preference",
)
DEFAULT_PREFERENCE_MARKERS = (
    "default technical-answer preference",
    "default debugging-answer behavior",
    "active preference",
    "future technical help",
    "future debugging help",
    "future technical answers",
    "future debugging answers",
)
DISTILLER_SCOPE_MARKER_GROUPS = {
    "short_term_distillation": SHORT_TERM_DISTILLATION_MARKERS,
    "short_term_observation": SHORT_TERM_OBSERVATION_MARKERS,
    "future_scope": FUTURE_SCOPE_MARKERS,
    "temporary_preference": TEMPORARY_PREFERENCE_MARKERS,
    "no_durable_preference": NO_DURABLE_PREFERENCE_MARKERS,
}
DISTILLER_PREFERENCE_MARKER_GROUPS = {
    "detailed": DETAILED_PREFERENCE_MARKERS,
    "concise": CONCISE_PREFERENCE_MARKERS,
    "negated_concise": NEGATED_CONCISE_MARKERS,
    "negated_detailed": NEGATED_DETAILED_MARKERS,
    "baseline_concise": CONCISE_BASELINE_MARKERS,
    "default_preference": DEFAULT_PREFERENCE_MARKERS,
    "separation": SEPARATION_MARKERS,
    "debugging": DEBUGGING_MARKERS,
}
