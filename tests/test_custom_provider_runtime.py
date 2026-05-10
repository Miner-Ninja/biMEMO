"""Tests for Custom LLM Provider runtime wiring."""

import sys

from backend.processes.orchestrator.dialog_recorder_setup import (
    _build_dialog_recorder_provider,
)
from backend.processes.orchestrator.llm_setup import _build_llm_client


class _Recorder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _CustomClient(_Recorder):
    pass


class _OpenRouterClient(_Recorder):
    pass


class _OllamaClient(_Recorder):
    pass


class _CustomProvider(_Recorder):
    pass


class _OpenRouterProvider(_Recorder):
    pass


def _ensure_process_helpers_parse_optional_int():
    """Patch leaked lightweight process_helpers stubs used by other tests."""
    module = sys.modules.get("backend.processes.orchestrator.process_helpers")
    if module is not None and not hasattr(module, "parse_optional_int"):
        module.parse_optional_int = lambda raw_value: (
            None if raw_value in (None, "") else int(str(raw_value))
        )


def test_custom_llm_client_uses_custom_runtime_class():
    """Verify custom chat wiring no longer instantiates the OpenRouter client."""
    _ensure_process_helpers_parse_optional_int()

    client = _build_llm_client(
        active_service_type="custom",
        llm_config={
            "api_key": "secret",
            "model": "qwen3.5:9b",
            "base_url": "https://ai.example.test",
            "num_ctx": "32768",
        },
        settings=object(),
        custom_client_cls=_CustomClient,
        openrouter_client_cls=_OpenRouterClient,
        ollama_client_cls=_OllamaClient,
    )

    assert isinstance(client, _CustomClient)
    assert not isinstance(client, _OpenRouterClient)
    assert client.kwargs["api_prefix"] == "/v1"
    assert client.kwargs["base_url"] == "https://ai.example.test"
    assert client.kwargs["num_ctx"] == 32768


def test_custom_dialog_recorder_uses_custom_provider_class():
    """Verify custom background wiring no longer instantiates OpenRouter provider."""
    _ensure_process_helpers_parse_optional_int()

    provider = _build_dialog_recorder_provider(
        llm_config={
            "api_key": "secret",
            "model": "qwen3.5:9b",
            "base_url": "https://ai.example.test",
            "dialog_recorder_base_url": "https://recorder.example.test",
            "embedding_base_url": "https://embed.example.test",
            "embedding_model": "bge-m3",
        },
        service_type="custom",
        custom_provider_cls=_CustomProvider,
        openrouter_provider_cls=_OpenRouterProvider,
        capabilities_registry=object(),
    )

    assert isinstance(provider, _CustomProvider)
    assert not isinstance(provider, _OpenRouterProvider)
    assert provider.kwargs["api_prefix"] == "/v1"
    assert provider.kwargs["base_url"] == "https://ai.example.test"
    assert provider.kwargs["dialog_recorder_base_url"] == (
        "https://recorder.example.test"
    )
    assert provider.kwargs["embedding_base_url"] == "https://embed.example.test"
