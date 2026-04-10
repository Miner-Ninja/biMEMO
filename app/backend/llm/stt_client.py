"""Speech-to-text client using OpenAI-compatible Whisper API.

Sends audio bytes to a /v1/audio/transcriptions endpoint via
multipart form upload and returns the transcribed text.
"""

import logging

import httpx


logger = logging.getLogger(__name__)

# Map MIME types to file extensions for the multipart upload
_MIME_TO_EXT = {
    "audio/ogg": "audio.ogg",
    "audio/webm": "audio.webm",
    "audio/mp3": "audio.mp3",
    "audio/mpeg": "audio.mp3",
    "audio/mp4": "audio.mp4",
    "audio/m4a": "audio.m4a",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
}

_PROMPT_HINTS = {
    "ru": "Привет, это голосовое сообщение.",
    "en": "Hello, this is a voice message.",
    "uk": "Привіт, це голосове повідомлення.",
    "lt": "Sveiki, tai balso žinutė.",
}

TRANSCRIPTION_TIMEOUT_SECONDS = 60.0


def _stt_prompt(language: str) -> str:
    """Return a short Whisper prompt hint for one language."""
    return _PROMPT_HINTS.get(language, "")


def _stt_payload(model: str, language: str, prompt: str) -> dict[str, str]:
    """Build the Whisper form payload."""
    data: dict[str, str] = {
        "model": model,
        "language": language,
        "response_format": "json",
    }
    if prompt:
        data["prompt"] = prompt
    return data


def _stt_filename(mime_type: str) -> str:
    """Return the upload filename to use for one audio MIME type."""
    return _MIME_TO_EXT.get(mime_type, "audio.bin")


class STTClient:
    """Async speech-to-text client for OpenAI-compatible Whisper API.

    Attributes:
        endpoint: Base URL of the Whisper API server.
        language: Language code for transcription (e.g. "en", "ru").
    """

    def __init__(self, endpoint: str, language: str, model: str) -> None:
        """Store endpoint and transcription defaults for Whisper requests."""
        self.endpoint = endpoint.rstrip("/")
        self.language = language
        self.model = model

    async def _transcription_response(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
    ) -> dict:
        """Send one transcription request and return the decoded JSON response."""
        url = f"{self.endpoint}/v1/audio/transcriptions"
        async with httpx.AsyncClient(timeout=TRANSCRIPTION_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                files={"file": (_stt_filename(mime_type), audio_bytes, mime_type)},
                data=_stt_payload(
                    self.model,
                    self.language,
                    _stt_prompt(self.language),
                ),
            )
            response.raise_for_status()
        return response.json()

    async def transcribe(self, audio_bytes: bytes, mime_type: str) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio data.
            mime_type: MIME type of the audio (e.g. "audio/webm", "audio/ogg").

        Returns:
            Transcribed text string.

        Raises:
            httpx.HTTPStatusError: If the API returns a non-2xx status.
            ValueError: If the response contains no text.
        """
        result = await self._transcription_response(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
        )
        text = result.get("text", "").strip()
        if not text:
            raise ValueError("STT returned empty transcription")

        logger.info(
            "Transcribed %d bytes (%s) → %d chars",
            len(audio_bytes),
            mime_type,
            len(text),
        )
        return text
