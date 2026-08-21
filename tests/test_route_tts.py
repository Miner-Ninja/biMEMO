"""Behaviour contract tests for the TTS routes (F27).

The routes expose exactly three states: TTS configured (200 with audio /
status payload), TTS not configured (503), and an empty synthesis result
(400). Tests call the route functions directly with a fake TTS client.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.processes.fastapi_app.routes.tts import (
    TTSRequest,
    _require_tts_client,
    synthesize,
    tts_status,
)


class _FakeTTSClient:
    """TTS client stand-in returning a canned synthesis result."""

    def __init__(self, result: bytes | None) -> None:
        self.result = result
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> bytes | None:
        self.calls.append(text)
        return self.result


WAV_BYTES = b"RIFF....WAVEfmt "


class TestRequireTTSClient:
    """The guard distinguishes configured from unconfigured TTS."""

    def test_none_raises_503(self):
        """An unconfigured TTS service is a 503, not a 500."""
        with pytest.raises(HTTPException) as excinfo:
            _require_tts_client(None)
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail == "TTS service not configured"

    def test_configured_client_is_returned(self):
        """The configured client is passed through unchanged."""
        client = _FakeTTSClient(WAV_BYTES)
        assert _require_tts_client(client) is client


class TestSynthesize:
    """POST /tts: audio bytes, or a precise error per state."""

    async def test_returns_wav_response(self):
        """A synthesis result is returned as audio/wav with the bytes."""
        client = _FakeTTSClient(WAV_BYTES)
        response = await synthesize(TTSRequest(text="hello"), claims=None, tts=client)
        assert response.media_type == "audio/wav"
        assert response.body == WAV_BYTES
        assert client.calls == ["hello"]

    async def test_none_result_is_400(self):
        """The client reporting no audio maps to 400, not a crash."""
        client = _FakeTTSClient(None)
        with pytest.raises(HTTPException) as excinfo:
            await synthesize(TTSRequest(text="hello"), claims=None, tts=client)
        assert excinfo.value.status_code == 400

    async def test_unconfigured_is_503(self):
        """No TTS client at all is a 503, not a 500."""
        with pytest.raises(HTTPException) as excinfo:
            await synthesize(TTSRequest(text="hello"), claims=None, tts=None)
        assert excinfo.value.status_code == 503


class TestTTSStatus:
    """GET /tts/status: availability without doing any synthesis."""

    async def test_configured_reports_available(self):
        """A configured TTS client reports the service as available."""
        response = await tts_status(claims=None, tts=_FakeTTSClient(WAV_BYTES))
        assert response == {"status": "available"}

    async def test_unconfigured_is_503(self):
        """An unconfigured TTS service reports 503."""
        with pytest.raises(HTTPException) as excinfo:
            await tts_status(claims=None, tts=None)
        assert excinfo.value.status_code == 503
