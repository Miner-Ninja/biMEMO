"""Custom LLM Provider chat client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from backend.constants import ENV_CUSTOM_NUM_CTX
from backend.llm.openai_compatible.capabilities import SOURCE_CUSTOM_MODELS
from backend.llm.openai_compatible.client import OpenAICompatibleClient
from backend.llm.openai_compatible.protocol import (
    CUSTOM_API_PREFIX,
    OpenAICompatibleClientError,
)


@dataclass
class CustomLLMClient(OpenAICompatibleClient):
    """Adapter for user-provided OpenAI-compatible LLM endpoints."""

    name: ClassVar[str] = "custom_llm"
    env_override_names: ClassVar[tuple[str, ...]] = (ENV_CUSTOM_NUM_CTX,)
    models_source: ClassVar[str] = SOURCE_CUSTOM_MODELS
    api_prefix: str = CUSTOM_API_PREFIX


CustomLLMClientError = OpenAICompatibleClientError


__all__ = [
    "CustomLLMClient",
    "CustomLLMClientError",
]
