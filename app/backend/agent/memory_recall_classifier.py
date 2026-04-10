"""Classify whether a user message requires memory recall.

Uses the dialog recorder model (small, fast) to determine if the
user is asking about past conversations, prior decisions, or
previously discussed topics. Works on any language.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = (
    "You are a classifier. Decide whether the user message asks about "
    "past conversations, prior decisions, previously discussed topics, "
    "or anything that was said in earlier chats.\n"
    "Answer ONLY 'yes' or 'no'. Nothing else."
)
CLASSIFIER_MAX_TOKENS = 5
CLASSIFIER_TEMPERATURE = 0.0
POSITIVE_RESPONSE = "yes"


async def classify_requires_memory_recall(
    message: str,
    provider: object | None,
) -> bool:
    """Determine if a user message needs memory recall.

    Args:
        message: Raw user message text.
        provider: LLM provider with dialog_recorder_model.

    Returns:
        True if memory recall should be forced.
    """
    if not message.strip() or provider is None:
        return False
    model = getattr(provider, "dialog_recorder_model", None)
    if not model:
        return False
    try:
        from backend.llm.ollama.models import ChatMessage

        messages = [
            ChatMessage(role="system", content=CLASSIFIER_SYSTEM_PROMPT),
            ChatMessage(role="user", content=message),
        ]
        response = await provider.chat(
            messages,
            model=model,
            max_tokens=CLASSIFIER_MAX_TOKENS,
            temperature=CLASSIFIER_TEMPERATURE,
            enable_thinking=False,
        )
        answer = (response.content or "").strip().lower()
        result = answer.startswith(POSITIVE_RESPONSE)
        logger.debug(
            "Memory recall classifier: %r → %s",
            message[:80],
            result,
        )
        return result
    except Exception:
        logger.warning(
            "Memory recall classification failed, skipping",
            exc_info=True,
        )
        return False
