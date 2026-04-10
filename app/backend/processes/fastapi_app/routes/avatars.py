"""Public avatar serving routes (no authentication required).

Provides a single GET endpoint for serving user and AI avatar images
stored in the database. Returns the raw image bytes with the correct
Content-Type header. Used by frontend components to display avatars
in chat messages, sidebar, and profile page.
"""

import base64

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..clients import AdminDBClient
from ..deps import _get_admin_client


avatars_router = APIRouter(prefix="/avatars")


@avatars_router.get("/{avatar_type}")
async def get_avatar(
    avatar_type: str,
    admin: AdminDBClient = Depends(_get_admin_client),  # noqa: B008
) -> Response:
    """Serve an avatar image from the database.

    Args:
        avatar_type: Avatar type ("user" or "ai").
        admin: DB Proxy client.

    Returns:
        Raw image response with correct Content-Type.

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
    return Response(content=data, media_type=avatar["mime_type"])
