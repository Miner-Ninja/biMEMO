"""Constants and low-level patterns for context assembly."""

from __future__ import annotations


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
