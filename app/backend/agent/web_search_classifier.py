"""Classify whether a user message requires real-time web search.

Uses the dialog recorder model (small, fast) to determine if the
user is asking about current data (news, prices, forecasts, events)
that requires a web search. Works on any language.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = (
    "You are a classifier. Decide whether the user message requires "
    "current data from the web to answer properly.\n"
    "Current data means: news, prices, forecasts, weather, scores, "
    "recent events, stock prices, or any information that changes "
    "frequently.\n"
    "Answer ONLY 'yes' or 'no'. Nothing else."
)
CLASSIFIER_MAX_TOKENS = 5
CLASSIFIER_TEMPERATURE = 0.0
POSITIVE_RESPONSE = "yes"


async def classify_requires_web_search(
    message: str,
    provider: object | None,
) -> bool:
    """Determine if a user message needs real-time web data.

    Args:
        message: Raw user message text.
        provider: LLM provider with dialog_recorder_model.

    Returns:
        True if web search should be forced.
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
            "Web search classifier: %r → %s",
            message[:80],
            result,
        )
        return result
    except Exception:
        logger.warning(
            "Web search classification failed, skipping",
            exc_info=True,
        )
        return False
