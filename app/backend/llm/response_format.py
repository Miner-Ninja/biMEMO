"""Resolve the preferred response transport for one provider/model pair."""

from __future__ import annotations

from backend.llm.capabilities import LlmResponseFormat


# --- Provider identifiers ---
PROVIDER_OLLAMA = "ollama"

# --- Exact provider/model overrides ---
EXACT_RESPONSE_FORMAT_OVERRIDES: dict[tuple[str, str], LlmResponseFormat] = {}

# --- Provider defaults used when no exact override exists ---
PROVIDER_DEFAULT_RESPONSE_FORMATS: dict[str, LlmResponseFormat] = {
    PROVIDER_OLLAMA: LlmResponseFormat.JSON_SCHEMA,
}


def resolve_llm_response_format(
    *,
    provider_name: str,
    model_name: str,
) -> LlmResponseFormat:
    """Resolve the preferred response format for one provider/model pair.

    Args:
        provider_name: Short provider identifier, for example ``"ollama"``.
        model_name: Provider-specific model identifier.

    Returns:
        The preferred LLM response format. Falls back to plain text when no
        exact override or provider default is configured.
    """
    exact_key = (provider_name, model_name)
    exact_override = EXACT_RESPONSE_FORMAT_OVERRIDES.get(exact_key)
    if exact_override is not None:
        return exact_override
    provider_default = PROVIDER_DEFAULT_RESPONSE_FORMATS.get(provider_name)
    if provider_default is not None:
        return provider_default
    return LlmResponseFormat.TEXT
