"""Shared yes/no LLM classifier helper."""

from __future__ import annotations

import logging
from dataclasses import dataclass


logger = logging.getLogger(__name__)

CLASSIFIER_MAX_TOKENS = 5
CLASSIFIER_TEMPERATURE = 0.0
POSITIVE_RESPONSE = "yes"
LOG_MESSAGE_PREVIEW_CHARS = 80


@dataclass(frozen=True)
class YesNoClassifierSpec:
    """Configuration for one yes/no classifier call.

    Attributes:
        system_prompt: System prompt that defines the classification task.
        debug_label: Short label used in debug logs.
        failure_label: Short label used in warning logs.
    """

    system_prompt: str
    debug_label: str
    failure_label: str


async def run_yes_no_classifier(
    *,
    message: str,
    provider: object | None,
    spec: YesNoClassifierSpec,
) -> bool:
    """Run one dialog-recorder yes/no classification request.

    Args:
        message: Raw user message text.
        provider: LLM provider with ``dialog_recorder_model`` and ``chat``.
        spec: Prompt and log labels for this classifier.

    Returns:
        True when the model response starts with ``yes``.
    """
    if not message.strip() or provider is None:
        return False
    model = getattr(provider, "dialog_recorder_model", None)
    if not model:
        return False
    try:
        from backend.llm.ollama.models import ChatMessage

        messages = [
            ChatMessage(role="system", content=spec.system_prompt),
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
            "%s classifier: %r -> %s",
            spec.debug_label,
            message[:LOG_MESSAGE_PREVIEW_CHARS],
            result,
        )
        return result
    except Exception:
        logger.warning(
            "%s classification failed, skipping",
            spec.failure_label,
            exc_info=True,
        )
        return False
