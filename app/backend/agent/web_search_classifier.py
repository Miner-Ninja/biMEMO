"""Classify whether a user message requires real-time web search.

Uses the dialog recorder model (small, fast) to determine if the
user is asking about current data (news, prices, forecasts, events)
that requires a web search. Works on any language.
"""

from __future__ import annotations

from backend.agent.yes_no_classifier import (
    YesNoClassifierSpec,
    run_yes_no_classifier,
)


CLASSIFIER_SYSTEM_PROMPT = (
    "You are a classifier. Decide whether the user message requires "
    "current data from the web to answer properly.\n"
    "Current data means: news, prices, forecasts, weather, scores, "
    "recent events, stock prices, or any information that changes "
    "frequently.\n"
    "Answer ONLY 'yes' or 'no'. Nothing else."
)
CLASSIFIER_SPEC = YesNoClassifierSpec(
    system_prompt=CLASSIFIER_SYSTEM_PROMPT,
    debug_label="Web search",
    failure_label="Web search",
)


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
    return await run_yes_no_classifier(
        message=message,
        provider=provider,
        spec=CLASSIFIER_SPEC,
    )
