"""Tests for the generic HTTP-based capability detector.

Covers the universal flow of ``detect_via_http`` independently of any
real provider: cached fast path, missing-then-warmup path, missing without
warmup, post-warmup miss, and HTTP errors at each step. Uses
``httpx.MockTransport`` and a fake ``HttpDetectorSpec`` so the tests
exercise only the generic layer.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from backend.llm.capabilities import ContextDetectionError
from backend.llm.kv_detector import (
    WARMUP_SUFFIX,
    HttpDetectorSpec,
    detect_via_http,
)


# --- Test fixtures ---
TARGET_MODEL = "fake-model"
EXPECTED_CTX = 16384
BASE_URL = "http://fake:1234"
PRIMARY_PATH = "/probe"
WARMUP_PATH = "/warmup"
SOURCE_LABEL = "fake:probe"


def _make_client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    """Build an httpx client bound to the mock transport."""
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


def _extract_with_value(value: int | None):
    """Build an extractor that returns ``value`` for any input."""

    def _extract(_body: dict[str, Any], _model: str) -> int | None:
        return value

    return _extract


def _extract_sequence(values: list[int | None]):
    """Build an extractor that returns the next value from ``values`` per call."""
    iterator = iter(values)

    def _extract(_body: dict[str, Any], _model: str) -> int | None:
        return next(iterator)

    return _extract


def _make_spec(
    *,
    extract,
    warmup=None,
    path: str = PRIMARY_PATH,
) -> HttpDetectorSpec:
    """Build an HttpDetectorSpec with sensible test defaults."""
    return HttpDetectorSpec(
        provider_name="fake",
        path=path,
        extract=extract,
        source_label=SOURCE_LABEL,
        warmup=warmup,
    )


@pytest.mark.asyncio
class TestPrimaryCallSucceeds:
    """When the primary call returns a value, no warmup runs."""

    async def test_returns_extracted_value(self) -> None:
        """A positive int from the extractor is returned with the spec label."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            return httpx.Response(200, json={"ok": True})

        client = _make_client(httpx.MockTransport(handler))
        spec = _make_spec(extract=_extract_with_value(EXPECTED_CTX))
        try:
            ctx, source = await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()

        assert ctx == EXPECTED_CTX
        assert source == SOURCE_LABEL
        assert calls == [f"GET {PRIMARY_PATH}"]


@pytest.mark.asyncio
class TestWarmupRetry:
    """First call returns None, warmup runs, retry returns the value."""

    async def test_warmup_then_success(self) -> None:
        """Extractor returns None first, value second; warmup runs in between."""
        calls: list[str] = []
        warmup_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            return httpx.Response(200, json={"ok": True})

        async def warmup(client: httpx.AsyncClient, model: str) -> None:
            warmup_calls.append(model)
            await client.post(WARMUP_PATH, json={"model": model})

        client = _make_client(httpx.MockTransport(handler))
        spec = _make_spec(
            extract=_extract_sequence([None, EXPECTED_CTX]),
            warmup=warmup,
        )
        try:
            ctx, source = await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()

        assert ctx == EXPECTED_CTX
        assert source == SOURCE_LABEL + WARMUP_SUFFIX
        assert warmup_calls == [TARGET_MODEL]
        assert calls == [
            f"GET {PRIMARY_PATH}",
            f"POST {WARMUP_PATH}",
            f"GET {PRIMARY_PATH}",
        ]

    async def test_warmup_then_still_none_raises(self) -> None:
        """Extractor returns None twice — even with warmup, raise."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        async def warmup(_client: httpx.AsyncClient, _model: str) -> None:
            return None

        client = _make_client(httpx.MockTransport(handler))
        spec = _make_spec(
            extract=_extract_sequence([None, None]),
            warmup=warmup,
        )
        try:
            with pytest.raises(ContextDetectionError) as exc_info:
                await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()

        assert exc_info.value.provider == "fake"
        assert exc_info.value.model == TARGET_MODEL
        assert SOURCE_LABEL in exc_info.value.attempted_sources
        assert SOURCE_LABEL + WARMUP_SUFFIX in exc_info.value.attempted_sources


@pytest.mark.asyncio
class TestNoWarmupRaisesImmediately:
    """When the spec has no warmup callable, missing → immediate raise."""

    async def test_missing_without_warmup_raises(self) -> None:
        """Spec without warmup raises after a single primary attempt."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            return httpx.Response(200, json={"ok": True})

        client = _make_client(httpx.MockTransport(handler))
        spec = _make_spec(extract=_extract_with_value(None), warmup=None)
        try:
            with pytest.raises(ContextDetectionError) as exc_info:
                await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()

        assert calls == [f"GET {PRIMARY_PATH}"]
        assert exc_info.value.attempted_sources == (SOURCE_LABEL,)

    async def test_zero_value_treated_as_missing(self) -> None:
        """Extractor value of 0 is non-positive and triggers raise / warmup."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        client = _make_client(httpx.MockTransport(handler))
        spec = _make_spec(extract=_extract_with_value(0), warmup=None)
        try:
            with pytest.raises(ContextDetectionError):
                await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()


@pytest.mark.asyncio
class TestHttpErrors:
    """HTTP failures at any step get wrapped in ContextDetectionError."""

    async def test_primary_returns_500(self) -> None:
        """A 500 from the primary call raises with the cause attached."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = _make_client(httpx.MockTransport(handler))
        spec = _make_spec(extract=_extract_with_value(EXPECTED_CTX))
        try:
            with pytest.raises(ContextDetectionError) as exc_info:
                await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()

        assert exc_info.value.cause is not None

    async def test_warmup_returns_500(self) -> None:
        """A 500 raised inside warmup is wrapped."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        async def warmup(client: httpx.AsyncClient, _model: str) -> None:
            response = await client.post(WARMUP_PATH)
            response.raise_for_status()

        # The mock transport always returns 200; force an error from warmup
        # by issuing a request and checking status manually.
        def warmup_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == WARMUP_PATH:
                return httpx.Response(500)
            return httpx.Response(200, json={"ok": True})

        client = _make_client(httpx.MockTransport(warmup_handler))
        spec = _make_spec(
            extract=_extract_with_value(None),
            warmup=warmup,
        )
        try:
            with pytest.raises(ContextDetectionError) as exc_info:
                await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()

        assert exc_info.value.cause is not None

    async def test_retry_after_warmup_returns_500(self) -> None:
        """A 500 on the retry after warmup is wrapped."""
        call_count = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(500)

        async def warmup(_client: httpx.AsyncClient, _model: str) -> None:
            return None

        client = _make_client(httpx.MockTransport(handler))
        spec = _make_spec(
            extract=_extract_with_value(None),
            warmup=warmup,
        )
        try:
            with pytest.raises(ContextDetectionError) as exc_info:
                await detect_via_http(client, TARGET_MODEL, spec)
        finally:
            await client.aclose()

        assert exc_info.value.cause is not None


@pytest.mark.asyncio
async def test_non_dict_body_passed_as_empty_dict() -> None:
    """A non-dict JSON body is normalized to an empty dict before extract."""
    seen_bodies: list[dict[str, Any]] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="not-a-dict")

    def extract(body: dict[str, Any], _model: str) -> int | None:
        seen_bodies.append(body)
        return EXPECTED_CTX

    client = _make_client(httpx.MockTransport(handler))
    spec = _make_spec(extract=extract)
    try:
        ctx, _source = await detect_via_http(client, TARGET_MODEL, spec)
    finally:
        await client.aclose()

    assert ctx == EXPECTED_CTX
    assert seen_bodies == [{}]
