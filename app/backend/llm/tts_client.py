"""Text-to-speech client using Fish Audio S2 Pro API.

Sends text to a /v1/tts endpoint and returns synthesized audio bytes.
Includes an in-memory LRU cache (max 50 entries) keyed by text hash.
"""

import hashlib
import logging
from collections import OrderedDict

import httpx


logger = logging.getLogger(__name__)

_MAX_CACHE_ENTRIES = 50
TTS_TIMEOUT_SECONDS = 30.0


def _cache_key(text: str) -> str:
    """Return the stable cache key for one normalized text string."""
    return hashlib.sha256(text.encode()).hexdigest()


def _cache_lookup(
    cache: OrderedDict[str, bytes],
    key: str,
) -> bytes | None:
    """Return one cached entry and mark it as recently used."""
    if key not in cache:
        return None
    cache.move_to_end(key)
    return cache[key]


def _cache_store(
    cache: OrderedDict[str, bytes],
    key: str,
    audio_bytes: bytes,
) -> None:
    """Store one audio result and trim the cache if needed."""
    cache[key] = audio_bytes
    if len(cache) > _MAX_CACHE_ENTRIES:
        cache.popitem(last=False)


def _tts_request_body(text: str, voice_id: str) -> dict[str, str]:
    """Build one Fish Audio TTS request body."""
    body: dict[str, str] = {"text": text}
    if voice_id:
        body["reference_id"] = voice_id
    return body


class TTSClient:
    """Async text-to-speech client for Fish Audio S2 Pro API.

    Attributes:
        endpoint: Base URL of the Fish Speech API server.
        voice_id: Reference voice ID for synthesis (empty = default voice).
    """

    def __init__(self, endpoint: str, voice_id: str = "") -> None:
        """Store endpoint, optional voice, and the in-memory audio cache."""
        self.endpoint = endpoint.rstrip("/")
        self.voice_id = voice_id
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    async def _synthesized_audio(self, text: str) -> bytes:
        """Send one TTS request and return the synthesized audio bytes."""
        url = f"{self.endpoint}/v1/tts"
        async with httpx.AsyncClient(timeout=TTS_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json=_tts_request_body(text, self.voice_id),
            )
            response.raise_for_status()
        return response.content

    async def synthesize(self, text: str) -> bytes | None:
        """Synthesize text to audio bytes.

        Args:
            text: Text to synthesize.

        Returns:
            Audio bytes on success, None if text is empty.

        Raises:
            httpx.HTTPStatusError: If the API returns a non-2xx status.
        """
        text = text.strip()
        if not text:
            return None

        cache_key = _cache_key(text)

        cached_audio = _cache_lookup(self._cache, cache_key)
        if cached_audio is not None:
            logger.debug("TTS cache hit for %s", cache_key[:12])
            return cached_audio

        audio_bytes = await self._synthesized_audio(text)

        _cache_store(self._cache, cache_key, audio_bytes)

        logger.info(
            "Synthesized %d chars → %d bytes audio",
            len(text),
            len(audio_bytes),
        )
        return audio_bytes
