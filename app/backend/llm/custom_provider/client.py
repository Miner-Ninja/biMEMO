"""Custom LLM Provider chat client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from backend.llm.openai_compatible.client import (
    CUSTOM_API_PREFIX,
    DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS,
    ENV_VAR_CUSTOM_NUM_CTX,
    SOURCE_CUSTOM_MODELS,
    SOURCE_ENV_OVERRIDE,
    SOURCE_PROVIDER_DEFAULT,
    SOURCE_UI_OVERRIDE,
    OpenAICompatibleClient,
    OpenAICompatibleClientError,
)


@dataclass
class CustomLLMClient(OpenAICompatibleClient):
    """Adapter for user-provided OpenAI-compatible LLM endpoints."""

    name: ClassVar[str] = "custom_llm"
    env_override_names: ClassVar[tuple[str, ...]] = (ENV_VAR_CUSTOM_NUM_CTX,)
    models_source: ClassVar[str] = SOURCE_CUSTOM_MODELS
    api_prefix: str = CUSTOM_API_PREFIX


CustomLLMClientError = OpenAICompatibleClientError


__all__ = [
    "CUSTOM_API_PREFIX",
    "DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS",
    "ENV_VAR_CUSTOM_NUM_CTX",
    "SOURCE_CUSTOM_MODELS",
    "SOURCE_ENV_OVERRIDE",
    "SOURCE_PROVIDER_DEFAULT",
    "SOURCE_UI_OVERRIDE",
    "CustomLLMClient",
    "CustomLLMClientError",
]
