"""Context-window capability detection for OpenAI-compatible providers.

Inputs for the resolution chain (env override, UI override, provider
``/models`` metadata, provider default) as source labels, the default
context size, and pure parsing helpers for ``/models`` entries. The
HTTP request itself is owned by the client; the branded adapters reuse
the labels and the env-override reader.
"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)

# --- Capability sources for OpenAI-compatible providers ---
SOURCE_ENV_OVERRIDE = "env-override"
SOURCE_UI_OVERRIDE = "ui-override"
SOURCE_CUSTOM_MODELS = "custom_llm:/v1/models"
SOURCE_PROVIDER_DEFAULT = "provider-default"
DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS = 8192
CONTEXT_WINDOW_FIELD_NAMES = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_context_tokens",
    "max_model_len",
    "max_model_length",
    "max_sequence_length",
    "max_position_embeddings",
    "n_ctx",
)
MODEL_ID_FIELD_NAMES = ("id", "name", "model", "root")


def _read_env_override(env_names: tuple[str, ...]) -> int | None:
    """Read provider env num_ctx variables and return a positive int.

    Treats unset, empty, non-integer, and non-positive values as "unset"
    so the priority chain proceeds to the UI override or provider default.
    """
    for env_name in env_names:
        raw = os.environ.get(env_name)
        if raw is None or raw.strip() == "":
            continue
        try:
            parsed = int(raw)
        except ValueError:
            logger.warning(
                "Invalid integer in %s=%r; ignoring override",
                env_name,
                raw,
            )
            continue
        if parsed > 0:
            return parsed
    return None


def _positive_int(value: object) -> int | None:
    """Return a positive integer parsed from one provider metadata value."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        parsed = int(value)
        return parsed if parsed > 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdecimal():
            parsed = int(normalized)
            return parsed if parsed > 0 else None
    return None


def _context_window_from_model_entry(entry: dict[str, object]) -> int | None:
    """Extract a context-window value from one OpenAI-compatible model entry."""
    for field_name in CONTEXT_WINDOW_FIELD_NAMES:
        parsed = _positive_int(entry.get(field_name))
        if parsed is not None:
            return parsed
    return None


def _model_entry_id_values(entry: dict[str, object]) -> set[str]:
    """Return all known string identifiers for one provider model entry."""
    return {
        value
        for field_name in MODEL_ID_FIELD_NAMES
        if isinstance((value := entry.get(field_name)), str) and value
    }


def _model_entry_matches(entry: dict[str, object], model: str) -> bool:
    """Return whether one provider model entry describes the requested model."""
    return model in _model_entry_id_values(entry)


def _model_entries(response_body: object) -> list[dict[str, object]]:
    """Return OpenAI-style model entries from one ``/models`` response body."""
    if not isinstance(response_body, dict):
        return []
    raw_entries = response_body.get("data")
    if not isinstance(raw_entries, list):
        raw_entries = response_body.get("models")
    if not isinstance(raw_entries, list):
        return []
    return [entry for entry in raw_entries if isinstance(entry, dict)]
