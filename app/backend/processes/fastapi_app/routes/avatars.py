"""Public avatar serving routes (no authentication required).

Provides a single GET endpoint for serving user and AI avatar images
stored in the database. Returns the raw image bytes with the correct
Content-Type header, a sha256-derived ETag and Cache-Control so clients
can issue conditional requests (If-None-Match -> 304). Used by frontend
components to display avatars in chat messages, sidebar, and profile page.
"""

import base64
import hashlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..deps import _get_admin_client
from .etag import if_none_match_matches


if TYPE_CHECKING:
    from ..clients import AdminDBClient


avatars_router = APIRouter(prefix="/avatars")

AVATAR_CACHE_CONTROL = "private, max-age=300"


def _avatar_etag(data: bytes) -> str:
    """Return the quoted sha256-hex ETag for one decoded avatar BLOB."""
    return f'"{hashlib.sha256(data).hexdigest()}"'


@avatars_router.get("/{avatar_type}")
async def get_avatar(
    avatar_type: str,
    request: Request,
    admin: AdminDBClient = Depends(_get_admin_client),
) -> Response:
    """Serve an avatar image from the database.

    Args:
        avatar_type: Avatar type ("user" or "ai").
        request: HTTP request (provides the If-None-Match header).
        admin: DB Proxy client.

    Returns:
        Raw image response with correct Content-Type, an ETag derived
        from the decoded BLOB and Cache-Control: private, max-age=300.
        A 304 with an empty body when If-None-Match matches the ETag.

    Raises:
        HTTPException: 404 if no avatar is set, 422 if invalid type.
    """
    if avatar_type not in {"user", "ai"}:
        raise HTTPException(status_code=422, detail="Invalid avatar type")

    result = await admin.get_avatar(avatar_type)
    avatar = result.get("avatar")
    if avatar is None:
        raise HTTPException(status_code=404, detail="Avatar not set")

    data = base64.b64decode(avatar["data"])
    etag = _avatar_etag(data)
    headers = {"etag": etag, "cache-control": AVATAR_CACHE_CONTROL}
    if if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=avatar["mime_type"], headers=headers)
