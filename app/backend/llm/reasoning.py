"""Defensive helpers for legacy reasoning markers in model output.

The canonical path is server-side reasoning parsing: vLLM, Ollama, OpenRouter
and other inference servers expose ``delta.reasoning_content`` (or
``message.thinking``) so the application receives visible content and
reasoning content as separate streams. The application no longer parses
``<think>...</think>`` markers from the visible text channel — that work
belongs to the inference server, which knows the format of its model.

These helpers remain only as a defensive cleanup for non-streaming responses
in case an upstream provider fails to strip a leaked reasoning block. They
must not be used to gate streaming output; doing so reintroduces the
buffering bug that this module's previous parser had on models without
``<think>`` markers (Gemma 4 and others).
"""

from __future__ import annotations


THINK_OPEN_TAG = "<think>"
THINK_CLOSE_TAG = "</think>"


def strip_reasoning_output(text: str) -> str:
    """Remove a leaked ``<think>...</think>`` block from one assembled text.

    Args:
        text: Full assistant text as assembled from one response.

    Returns:
        The visible portion after the closing tag, or an empty string when
        only an opening tag is present, or the original text when no marker
        is found.
    """
    if THINK_CLOSE_TAG in text:
        _, visible_text = text.split(THINK_CLOSE_TAG, 1)
        return visible_text.lstrip()
    if THINK_OPEN_TAG in text:
        return ""
    return text
