"""Resolve the preferred response transport for one provider/model pair."""

from __future__ import annotations

from backend.config import get_settings
from backend.llm.capabilities import LlmResponseFormat


# --- Exact provider/model overrides ---
EXACT_RESPONSE_FORMAT_OVERRIDES: dict[tuple[str, str], LlmResponseFormat] = {}


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
        The preferred LLM response format. An exact ``(provider, model)``
        override wins when present; otherwise the global setting driven
        by ``BIMEMO_LLM_RESPONSE_FORMAT`` is used.
    """
    exact_key = (provider_name, model_name)
    exact_override = EXACT_RESPONSE_FORMAT_OVERRIDES.get(exact_key)
    if exact_override is not None:
        return exact_override
    return LlmResponseFormat(get_settings().llm_response_format)
