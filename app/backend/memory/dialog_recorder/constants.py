"""Constants used by the dialog recorder pipeline."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.memory.models import RecordKind


DIALOG_RECORDER_SYSTEM_PROMPT = (
    "You are a compression agent. Given conversation messages, "
    "produce a structured record.\n"
    "Rules:\n"
    "- summary: concise summary (max 500 chars)\n"
    "- key_facts: list of important facts (max 10 items)\n"
    "- action_items: list of actionable items (max 10 items)\n"
    "- memory_kinds: include only categories that are genuinely present\n"
    "- fact: new factual information worth remembering later\n"
    "- preference: user preference or answer-style preference\n"
    "- action: concrete next step, task, or follow-up commitment\n"
    "- conflict: new information that changes or contradicts prior understanding\n"
    "- Preserve numbers, dates, and specific details\n"
    "- Write summary, key_facts, and action_items in the same primary "
    "language as the input messages\n"
    "- Do not translate Russian input into English\n"
    "- If there is no meaningful new fact, preference, action, or conflict, "
    "leave MEMORY_KINDS empty\n"
    "- Output ONLY this plain text format, no JSON and no extra commentary:\n"
    "SUMMARY: <text>\n"
    "KEY_FACTS:\n"
    "- <fact 1>\n"
    "- <fact 2>\n"
    "ACTION_ITEMS:\n"
    "- <action 1>\n"
    "- <action 2>\n"
    "MEMORY_KINDS:\n"
    "- <fact|preference|action|conflict>\n"
    "/no_think"
)
MAX_FILTERED_FIELD_ITEMS = 10
PREFERENCE_KEYWORDS = (
    "prefer",
    "preference",
    "likes",
    "wants",
    "style",
    "короче",
    "кратк",
    "подроб",
    "предпоч",
    "нравит",
    "ответ",
    "стиль",
)
CONFLICT_KEYWORDS = (
    "change",
    "changed",
    "instead",
    "now",
    "not anymore",
    "conflict",
    "contradict",
    "switch",
    "теперь",
    "вместо",
    "измен",
    "противореч",
    "больше не",
)
ACTION_KEYWORDS = (
    "todo",
    "next step",
    "follow up",
    "need to",
    "should",
    "must",
    "task",
    "action",
    "сделать",
    "нужно",
    "надо",
    "дальше",
    "следующ",
    "задач",
)
MEMORY_RECAP_QUESTION_MARKERS = (
    "what do you remember",
    "what should you remember",
    "repeat my current",
    "what answer style do i prefer now",
    "what is the active preference",
    "what is the durable preference",
    "what part of this conversation should remain",
    "which parts should remain long-term",
    "if i return tomorrow, what answer style should you still remember",
    "which preference should still matter after the validation run is over",
    "what should you remember about my current answer style in future replies",
    "summarize my long-term preference",
    "summarize the short-term validation context",
    "state the final active long-term preference",
    "what should be retrieved in a future technical answer from this session",
    "what should not dominate future retrieval once this test run is over",
)
MEMORY_RECAP_SCOPE_MARKERS = (
    "long-term",
    "long term",
    "short-term",
    "short term",
    "future replies",
    "future conversations",
    "future retrieval",
    "durable preference",
    "current answer style",
)
MEMORY_BOUNDARY_MARKERS = (
    "temporary detail",
    "fade later",
    "temporary test context",
    "durable answer-style preference",
    "durable answer style preference",
    "permanent preference",
    "permanent change",
    "session ends",
    "session end",
    "session-specific",
    "current session only",
    "validation run",
    "will be discarded",
    "won't persist",
    "will not persist",
    "no permanent change",
)
GENERIC_SUMMARY_PHRASES = (
    "discussed",
    "asked for help",
    "asked a question",
    "provided an example",
    "requested a code example",
    "обсуждали",
    "задал вопрос",
    "попросил пример",
    "показал пример",
)
RECENT_OBSERVATION_DEDUP_LIMIT = 20
RECENT_REFLECTION_DEDUP_LIMIT = 10
MIN_DUPLICATE_TOKEN_OVERLAP = 0.75
DIALOG_RECORDER_MAX_TOKENS = 160
DIALOG_RECORDER_TEMPERATURE = 0.0
DIALOG_RECORDER_TRACE_DECISIONS = {
    "stored": "record_stored_by_llm_memory_judgment",
    "rate_limited": "record_skipped_by_rate_limit",
    "llm_error": "record_skipped_by_llm_error",
    "parse_failed": "record_skipped_by_llm_output_parse_failure",
    "not_memory_worthy": "record_skipped_by_llm_not_memory_worthy_judgment",
    "duplicate_memory": "record_rejected_by_duplicate_check",
    "unsafe_output": "record_rejected_by_output_filter",
    "deleted_conversation": "record_skipped_by_deleted_conversation",
}
TRACE_DECISION_STORED = DIALOG_RECORDER_TRACE_DECISIONS["stored"]
TRACE_DECISION_RATE_LIMITED = DIALOG_RECORDER_TRACE_DECISIONS["rate_limited"]
TRACE_DECISION_LLM_ERROR = DIALOG_RECORDER_TRACE_DECISIONS["llm_error"]
TRACE_DECISION_PARSE_FAILED = DIALOG_RECORDER_TRACE_DECISIONS["parse_failed"]
TRACE_DECISION_NOT_MEMORY_WORTHY = DIALOG_RECORDER_TRACE_DECISIONS["not_memory_worthy"]
TRACE_DECISION_DUPLICATE_MEMORY = DIALOG_RECORDER_TRACE_DECISIONS["duplicate_memory"]
TRACE_DECISION_UNSAFE_OUTPUT = DIALOG_RECORDER_TRACE_DECISIONS["unsafe_output"]
TRACE_DECISION_DELETED_CONVERSATION = DIALOG_RECORDER_TRACE_DECISIONS[
    "deleted_conversation"
]
MAX_SYNTHETIC_ACTION_ITEMS = 2
CYRILLIC_CODEPOINT_START = ord("\u0430")
CYRILLIC_CODEPOINT_END = ord("\u044f")
DIALOG_RECORDER_ACTION_TEMPLATES = {
    "ru": {
        "preference": (
            "\u0423\u0447\u0438\u0442\u044b\u0432\u0430\u0442\u044c "
            "\u043f\u0440\u0435\u0434\u043f\u043e\u0447\u0442\u0435\u043d\u0438\u0435 "
            "\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f: "
            "{summary}"
        ),
        "follow_up": (
            "\u0421\u0434\u0435\u043b\u0430\u0442\u044c "
            "\u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 "
            "\u0448\u0430\u0433: {summary}"
        ),
        "conflict": (
            "\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c "
            "\u043f\u0430\u043c\u044f\u0442\u044c "
            "\u0441 \u0443\u0447\u0435\u0442\u043e\u043c "
            "\u043d\u043e\u0432\u043e\u0433\u043e "
            "\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f: {summary}"
        ),
    },
    "en": {
        "preference": "Honor the user's preference: {summary}",
        "follow_up": "Follow up on: {summary}",
        "conflict": "Update memory with the latest change: {summary}",
    },
}
RUSSIAN_PREFERENCE_ACTION_TEMPLATE = DIALOG_RECORDER_ACTION_TEMPLATES["ru"][
    "preference"
]
RUSSIAN_FOLLOW_UP_ACTION_TEMPLATE = DIALOG_RECORDER_ACTION_TEMPLATES["ru"]["follow_up"]
RUSSIAN_CONFLICT_ACTION_TEMPLATE = DIALOG_RECORDER_ACTION_TEMPLATES["ru"]["conflict"]
ENGLISH_PREFERENCE_ACTION_TEMPLATE = DIALOG_RECORDER_ACTION_TEMPLATES["en"][
    "preference"
]
ENGLISH_FOLLOW_UP_ACTION_TEMPLATE = DIALOG_RECORDER_ACTION_TEMPLATES["en"]["follow_up"]
ENGLISH_CONFLICT_ACTION_TEMPLATE = DIALOG_RECORDER_ACTION_TEMPLATES["en"]["conflict"]
DIALOG_RECORDER_OUTPUT_FORMAT = {
    "summary_prefix": "SUMMARY:",
    "key_facts_header": "KEY_FACTS:",
    "action_items_header": "ACTION_ITEMS:",
    "memory_kinds_header": "MEMORY_KINDS:",
    "list_item_prefix": "- ",
}
DIALOG_RECORDER_SUMMARY_PREFIX = DIALOG_RECORDER_OUTPUT_FORMAT["summary_prefix"]
DIALOG_RECORDER_KEY_FACTS_HEADER = DIALOG_RECORDER_OUTPUT_FORMAT["key_facts_header"]
DIALOG_RECORDER_ACTION_ITEMS_HEADER = DIALOG_RECORDER_OUTPUT_FORMAT[
    "action_items_header"
]
DIALOG_RECORDER_MEMORY_KINDS_HEADER = DIALOG_RECORDER_OUTPUT_FORMAT[
    "memory_kinds_header"
]
DIALOG_RECORDER_LIST_ITEM_PREFIX = DIALOG_RECORDER_OUTPUT_FORMAT["list_item_prefix"]

ROLE_PREFIX_REPLACEMENTS_FOR_FILTER = {
    "User:": "User said -",
    "Assistant:": "Assistant said -",
    "System:": "System said -",
}
ROLE_PREFIXES_FOR_FILTER = tuple(ROLE_PREFIX_REPLACEMENTS_FOR_FILTER)

EMPTY_MEMORY_KINDS: list[RecordKind] = []
