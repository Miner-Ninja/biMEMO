"""Constants and low-level patterns for context assembly."""

from __future__ import annotations

import re


TOKEN_RE = re.compile(r"\w+", re.UNICODE)
LOW_SIGNAL_TOKENS = {
    "about",
    "again",
    "been",
    "from",
    "have",
    "into",
    "just",
    "that",
    "this",
    "with",
    "were",
    "what",
    "when",
    "where",
    "which",
    "would",
    "говорили",
    "когда",
    "между",
    "можно",
    "после",
    "потом",
    "помнишь",
    "про",
    "снова",
    "такое",
    "тогда",
    "только",
    "хочу",
    "чтобы",
    "этого",
}

MIN_MEMORY_BUDGET = 2048
MAX_MEMORY_BUDGET = 12000
MEMORY_WINDOW_RATIO = 0.30
OPERATIONAL_TOKEN_RESERVE = 2048
DEFAULT_MEMORY_CAP_RATIOS = {
    "messages": 0.5,
    "retrieved_messages": 0.15,
    "observations": 0.20,
    "reflections": 0.15,
}
MEMORY_QUERY_CAP_RATIOS = {
    "messages": 0.35,
    "retrieved_messages": 0.20,
    "observations": 0.25,
    "reflections": 0.20,
}


def _cap_ratio(
    ratios: dict[str, float],
    key: str,
) -> float:
    """Return one named memory-cap ratio from a ratio group."""
    return ratios[key]


MESSAGE_TOKEN_CAP_RATIO = _cap_ratio(DEFAULT_MEMORY_CAP_RATIOS, "messages")
RETRIEVED_MSG_TOKEN_CAP_RATIO = _cap_ratio(
    DEFAULT_MEMORY_CAP_RATIOS,
    "retrieved_messages",
)
OBSERVATION_TOKEN_CAP_RATIO = _cap_ratio(DEFAULT_MEMORY_CAP_RATIOS, "observations")
DISTILLATION_TOKEN_CAP_RATIO = _cap_ratio(DEFAULT_MEMORY_CAP_RATIOS, "reflections")

MEMORY_QUERY_MESSAGE_TOKEN_CAP_RATIO = _cap_ratio(
    MEMORY_QUERY_CAP_RATIOS,
    "messages",
)
MEMORY_QUERY_RETRIEVED_MSG_TOKEN_CAP_RATIO = _cap_ratio(
    MEMORY_QUERY_CAP_RATIOS,
    "retrieved_messages",
)
MEMORY_QUERY_OBSERVATION_TOKEN_CAP_RATIO = _cap_ratio(
    MEMORY_QUERY_CAP_RATIOS,
    "observations",
)
MEMORY_QUERY_DISTILLATION_TOKEN_CAP_RATIO = _cap_ratio(
    MEMORY_QUERY_CAP_RATIOS,
    "reflections",
)
AUTO_COMPRESSION_TRIGGER_RATIO = 0.8
AUTO_COMPRESSION_MIN_TOKEN_BUDGET = 2000
COMPRESSED_MESSAGE_CAP_RATIO = 0.55
MIN_RAW_MESSAGES_TO_KEEP = 6
MAX_DIALOG_SUMMARY_SNIPPETS = 4
MAX_DIALOG_SUMMARY_SNIPPET_CHARS = 160

MAX_RETRIEVED_CONTENT_CHARS = 2000

# --- Zone-based context budget (percentage of available tokens) ---
WORKING_MEMORY_ZONE_RATIO = 0.55
RECALL_RESULTS_ZONE_RATIO = 0.25
CONVERSATION_SUMMARY_ZONE_RATIO = 0.20
DEFAULT_EVICTION_RATIO = 0.65
RESPONSE_TOKEN_RESERVE_RATIO = 0.15
CHARS_PER_TOKEN_ESTIMATE = 4

# --- S-zone LLM compression ---
S_ZONE_COMPRESSION_MAX_TOKENS = 200

S_ZONE_COMPRESSION_SYSTEM_PROMPT = (
    "You are a conversation summarizer. "
    "Produce a concise structured summary of the conversation segment. "
    "Use exactly this format:\n"
    "Goal: <what the user is trying to accomplish>\n"
    "Progress: <what has been done so far>\n"
    "Decisions: <important choices made>\n"
    "Resources: <files, tools, or systems mentioned>\n"
    "Next: <what remains to be done>\n"
    "Keep each section to one sentence. "
    "Omit sections that have no relevant information. "
    "Write in the same language as the conversation."
)

S_ZONE_UPDATE_INSTRUCTION = (
    "Below is the existing conversation summary. "
    "Update it with the new segment without losing previous information. "
    "Merge, do not replace.\n\n"
    "Existing summary:\n"
)


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)
