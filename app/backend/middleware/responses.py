"""Shared ASGI response helpers for middleware."""

from typing import TYPE_CHECKING

from backend.processes.fastapi_app.problem import PROBLEM_MEDIA_TYPE


if TYPE_CHECKING:
    from starlette.types import Send


def _json_headers(
    body: bytes,
    extra_headers: list[list[bytes]] | None = None,
) -> list[list[bytes]]:
    """Build the standard header list for one problem+json ASGI response."""
    headers: list[list[bytes]] = [
        [b"content-type", PROBLEM_MEDIA_TYPE.encode()],
        [b"content-length", str(len(body)).encode()],
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return headers


def _response_start_message(
    *,
    status: int,
    body: bytes,
    extra_headers: list[list[bytes]] | None = None,
) -> dict[str, object]:
    """Build one ASGI response-start message for a problem+json body."""
    return {
        "type": "http.response.start",
        "status": status,
        "headers": _json_headers(body, extra_headers),
    }


async def send_json_error(
    send: Send,
    status: int,
    body: bytes,
    extra_headers: list[list[bytes]] | None = None,
) -> None:
    """Send a problem+json error response via raw ASGI.

    Args:
        send: ASGI send callable.
        status: HTTP status code.
        body: Pre-encoded problem body bytes (see ``problem_body``).
        extra_headers: Optional additional headers.
    """
    await send(
        _response_start_message(
            status=status,
            body=body,
            extra_headers=extra_headers,
        )
    )
    await send({"type": "http.response.body", "body": body})
