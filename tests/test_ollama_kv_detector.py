"""Unit tests for the Ollama-specific kv detector spec.

Covers ``_extract_ollama`` (matching by name/model field, ``:latest``
suffix handling, missing/zero/non-dict cases) and ``_warmup_ollama``
(POST body shape, HTTP error propagation). The generic detector flow is
covered separately in ``test_kv_detector.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from backend.llm.ollama.kv_detector import (
    GENERATE_ENDPOINT,
    OLLAMA_DETECTOR,
    PS_ENDPOINT,
    SOURCE_PS,
    WARMUP_KEEP_ALIVE,
    _extract_ollama,
    _warmup_ollama,
)


# --- Test fixtures ---
TARGET_MODEL = "qwen3:8b"
EXPECTED_CTX = 32768


def _entry(
    name: str | None = None,
    model: str | None = None,
    ctx: object = None,
) -> dict[str, Any]:
    """Build a single Ollama /api/ps model entry."""
    entry: dict[str, Any] = {}
    if name is not None:
        entry["name"] = name
    if model is not None:
        entry["model"] = model
    if ctx is not None:
        entry["context_length"] = ctx
    return entry


def _ps_body(*entries: dict[str, Any]) -> dict[str, Any]:
    """Build a /api/ps response body wrapping the given entries."""
    return {"models": list(entries)}


class TestExtractOllama:
    """Pure-function tests for the Ollama extractor."""

    def test_match_by_name(self) -> None:
        """Entry with matching ``name`` returns its context_length."""
        body = _ps_body(_entry(name=TARGET_MODEL, ctx=EXPECTED_CTX))
        assert _extract_ollama(body, TARGET_MODEL) == EXPECTED_CTX

    def test_match_by_model_field_when_name_missing(self) -> None:
        """Entry without ``name`` falls back to ``model`` field."""
        body = _ps_body(_entry(model=TARGET_MODEL, ctx=EXPECTED_CTX))
        assert _extract_ollama(body, TARGET_MODEL) == EXPECTED_CTX

    def test_match_with_latest_suffix_on_reported(self) -> None:
        """Reported ``model:latest`` matches a bare target."""
        body = _ps_body(_entry(name=f"{TARGET_MODEL}:latest", ctx=EXPECTED_CTX))
        assert _extract_ollama(body, TARGET_MODEL) == EXPECTED_CTX

    def test_match_with_latest_suffix_on_target(self) -> None:
        """Bare reported name matches a target with ``:latest`` suffix."""
        body = _ps_body(_entry(name="qwen3:8b", ctx=EXPECTED_CTX))
        assert _extract_ollama(body, "qwen3:8b:latest") == EXPECTED_CTX

    def test_no_matching_entry_returns_none(self) -> None:
        """Body without the requested model yields None."""
        body = _ps_body(_entry(name="other-model", ctx=EXPECTED_CTX))
        assert _extract_ollama(body, TARGET_MODEL) is None

    def test_zero_context_length_treated_as_missing(self) -> None:
        """An entry with zero context_length is not accepted."""
        body = _ps_body(_entry(name=TARGET_MODEL, ctx=0))
        assert _extract_ollama(body, TARGET_MODEL) is None

    def test_negative_context_length_treated_as_missing(self) -> None:
        """An entry with negative context_length is not accepted."""
        body = _ps_body(_entry(name=TARGET_MODEL, ctx=-1))
        assert _extract_ollama(body, TARGET_MODEL) is None

    def test_non_int_context_length_treated_as_missing(self) -> None:
        """A string context_length is rejected."""
        body = _ps_body(_entry(name=TARGET_MODEL, ctx="32768"))
        assert _extract_ollama(body, TARGET_MODEL) is None

    def test_missing_context_length_returns_none(self) -> None:
        """An entry without context_length yields None."""
        body = _ps_body(_entry(name=TARGET_MODEL))
        assert _extract_ollama(body, TARGET_MODEL) is None

    def test_non_list_models_returns_none(self) -> None:
        """If ``models`` is not a list, the extractor yields None."""
        body: dict[str, Any] = {"models": "not-a-list"}
        assert _extract_ollama(body, TARGET_MODEL) is None

    def test_non_dict_entries_skipped(self) -> None:
        """Non-dict items inside the models list are ignored."""
        valid = _entry(name=TARGET_MODEL, ctx=EXPECTED_CTX)
        body: dict[str, Any] = {"models": ["junk", 42, valid]}
        assert _extract_ollama(body, TARGET_MODEL) == EXPECTED_CTX

    def test_entry_missing_both_name_and_model_skipped(self) -> None:
        """An entry with neither ``name`` nor ``model`` is ignored."""
        body = _ps_body(_entry(ctx=EXPECTED_CTX))
        assert _extract_ollama(body, TARGET_MODEL) is None

    def test_empty_body_returns_none(self) -> None:
        """An empty dict body yields None without crashing."""
        assert _extract_ollama({}, TARGET_MODEL) is None


@pytest.mark.asyncio
class TestWarmupOllama:
    """HTTP integration tests for the Ollama warmup callable."""

    async def test_posts_correct_body(self) -> None:
        """Warmup issues a POST to /api/generate with the documented body."""
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == GENERATE_ENDPOINT
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"done": True})

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://ollama:11434",
        )
        try:
            await _warmup_ollama(client, TARGET_MODEL)
        finally:
            await client.aclose()

        assert seen == [
            {
                "model": TARGET_MODEL,
                "prompt": "",
                "stream": False,
                "keep_alive": WARMUP_KEEP_ALIVE,
            }
        ]

    async def test_propagates_http_error(self) -> None:
        """A non-2xx response from /api/generate raises HTTPError."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://ollama:11434",
        )
        try:
            with pytest.raises(httpx.HTTPError):
                await _warmup_ollama(client, TARGET_MODEL)
        finally:
            await client.aclose()


class TestOllamaDetectorConstant:
    """Sanity checks on the OLLAMA_DETECTOR singleton spec."""

    def test_provider_name(self) -> None:
        """The detector identifies itself as 'ollama'."""
        assert OLLAMA_DETECTOR.provider_name == "ollama"

    def test_primary_path_is_api_ps(self) -> None:
        """The primary endpoint is /api/ps."""
        assert OLLAMA_DETECTOR.path == PS_ENDPOINT

    def test_source_label_matches(self) -> None:
        """The source label is the documented constant."""
        assert OLLAMA_DETECTOR.source_label == SOURCE_PS

    def test_warmup_is_wired(self) -> None:
        """The warmup callable is wired to the Ollama implementation."""
        assert OLLAMA_DETECTOR.warmup is _warmup_ollama

    def test_extract_is_wired(self) -> None:
        """The extractor is wired to the Ollama implementation."""
        assert OLLAMA_DETECTOR.extract is _extract_ollama
