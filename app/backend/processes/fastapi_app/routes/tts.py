"""Text-to-speech routes."""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..deps import _require_auth


if TYPE_CHECKING:
    from backend.channels.jwt_auth import TokenClaims
    from backend.llm.tts_client import TTSClient


logger = logging.getLogger(__name__)

tts_router = APIRouter()


class TTSRequest(BaseModel):
    """Request body for text-to-speech synthesis."""

    text: str


def _get_tts_client(request: Request) -> TTSClient | None:
    """Return the TTS client from app state, or None if not configured."""
    return getattr(request.app.state, "tts_client", None)


def _require_tts_client(tts: TTSClient | None) -> TTSClient:
    """Return the configured TTS client or fail when TTS is unavailable."""
    if tts is None:
        raise HTTPException(
            status_code=503,
            detail="TTS service not configured",
        )
    return tts


def _tts_status_response() -> dict[str, str]:
    """Build the standard TTS availability response."""
    return {"status": "available"}


def _tts_audio_response(audio: bytes) -> Response:
    """Build the standard TTS audio response."""
    return Response(
        content=audio,
        media_type="audio/wav",
    )


@tts_router.post("/tts")
async def synthesize(
    body: TTSRequest,
    claims: TokenClaims = Depends(_require_auth),
    tts: TTSClient | None = Depends(_get_tts_client),
) -> Response:
    """Synthesize text to speech audio.

    Returns audio bytes with appropriate Content-Type header.
    """
    tts_client = _require_tts_client(tts)
    audio = await tts_client.synthesize(body.text)
    if audio is None:
        raise HTTPException(
            status_code=400,
            detail="Empty text provided",
        )

    return _tts_audio_response(audio)


@tts_router.get("/tts/status")
async def tts_status(
    claims: TokenClaims = Depends(_require_auth),
    tts: TTSClient | None = Depends(_get_tts_client),
) -> dict:
    """Check TTS availability.

    Returns 200 if TTS is configured, 503 if not.
    """
    _require_tts_client(tts)
    return _tts_status_response()
