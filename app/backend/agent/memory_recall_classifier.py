"""Classify whether a user message requires memory recall.

Uses the dialog recorder model (small, fast) to determine if the
user is asking about past conversations, prior decisions, or
previously discussed topics. Works on any language.
"""

from __future__ import annotations

from backend.agent.yes_no_classifier import (
    YesNoClassifierSpec,
    run_yes_no_classifier,
)


CLASSIFIER_SYSTEM_PROMPT = (
    "You are a classifier. Decide whether the user message asks about "
    "past conversations, prior decisions, previously discussed topics, "
    "or anything that was said in earlier chats.\n"
    "Answer ONLY 'yes' or 'no'. Nothing else."
)
CLASSIFIER_SPEC = YesNoClassifierSpec(
    system_prompt=CLASSIFIER_SYSTEM_PROMPT,
    debug_label="Memory recall",
    failure_label="Memory recall",
)


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
    return await run_yes_no_classifier(
        message=message,
        provider=provider,
        spec=CLASSIFIER_SPEC,
    )
