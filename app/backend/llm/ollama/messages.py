"""Helpers for converting multimodal messages into Ollama chat payloads."""

from __future__ import annotations

from typing import Any


DATA_URL_PREFIX = "data:"
DATA_URL_SEPARATOR = ","
IMAGE_URL_PART_TYPE = "image_url"
TEXT_PART_TYPE = "text"


def _copy_message_metadata(message: dict[str, Any]) -> dict[str, Any]:
    """Copy non-content fields from one OpenAI-style message."""
    return {
        key: value for key, value in message.items() if key not in {"content", "images"}
    }


def _extract_base64_payload(data_url: str) -> str:
    """Extract the base64 payload from a validated image data URL."""
    if not data_url.startswith(DATA_URL_PREFIX) or DATA_URL_SEPARATOR not in data_url:
        raise ValueError("Ollama image inputs must be data URLs")
    _, base64_payload = data_url.split(DATA_URL_SEPARATOR, maxsplit=1)
    if not base64_payload:
        raise ValueError("Ollama image data URL is missing base64 payload")
    return base64_payload


def convert_message_for_ollama(message: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI-style multimodal message into Ollama format."""
    message_payload = _copy_message_metadata(message)
    content = message.get("content", "")
    if not isinstance(content, list):
        message_payload["content"] = content
        _copy_existing_images(message, message_payload)
        return message_payload

    text_parts: list[str] = []
    image_payloads: list[str] = []
    _collect_ollama_content_parts(content, text_parts, image_payloads)

    message_payload["content"] = "\n".join(text_parts)
    if image_payloads:
        message_payload["images"] = image_payloads
    return message_payload


def _copy_existing_images(
    message: dict[str, Any],
    message_payload: dict[str, Any],
) -> None:
    """Copy prebuilt image payloads into one Ollama message."""
    existing_images = message.get("images")
    if isinstance(existing_images, list):
        message_payload["images"] = existing_images


def _text_part_value(part: dict[str, Any]) -> str | None:
    """Return the text value from one multimodal content part."""
    if part.get("type") != TEXT_PART_TYPE:
        return None
    return str(part.get("text", ""))


def _image_part_value(part: dict[str, Any]) -> str | None:
    """Return the Ollama-ready image payload from one multimodal content part."""
    if part.get("type") != IMAGE_URL_PART_TYPE:
        return None
    image_info = part.get("image_url", {})
    if not isinstance(image_info, dict):
        return None
    image_url = image_info.get("url", "")
    if not isinstance(image_url, str):
        return None
    return _extract_base64_payload(image_url)


def _collect_ollama_content_parts(
    content: list[Any],
    text_parts: list[str],
    image_payloads: list[str],
) -> None:
    """Collect text and image payloads from one multimodal content list."""
    for part in content:
        if not isinstance(part, dict):
            continue
        text_value = _text_part_value(part)
        if text_value is not None:
            text_parts.append(text_value)
            continue
        image_value = _image_part_value(part)
        if image_value is not None:
            image_payloads.append(image_value)


def convert_messages_for_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert a message history into Ollama-compatible chat payloads."""
    return [convert_message_for_ollama(message) for message in messages]
