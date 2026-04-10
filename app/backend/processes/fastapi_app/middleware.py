"""Cookie helpers for auth responses."""

import logging

from fastapi import HTTPException, Request, Response


logger = logging.getLogger(__name__)

_REQUIRED_AUTH_TOKEN_KEYS = ("access_token", "refresh_token")


def _cookie_secure(request: Request) -> bool:
    """Return False in debug mode so cookies work over plain HTTP."""
    settings = getattr(request.app.state, "settings", None)
    return not (settings is not None and getattr(settings, "debug", False))


def _missing_auth_token_keys(tokens: dict) -> list[str]:
    """Return required auth-token keys missing from one token payload."""
    return [key for key in _REQUIRED_AUTH_TOKEN_KEYS if key not in tokens]


def _auth_cookie_options(secure: bool) -> dict[str, object]:
    """Return the shared options for auth cookies."""
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "strict",
        "path": "/",
    }


def _set_auth_cookie(
    response: Response,
    *,
    key: str,
    value: str,
    secure: bool,
) -> None:
    """Set one auth cookie with the shared options."""
    response.set_cookie(
        key=key,
        value=value,
        **_auth_cookie_options(secure),
    )


def _set_auth_cookies(
    response: Response,
    tokens: dict,
    secure: bool,
) -> None:
    """Set access and refresh token cookies on the response.

    Args:
        response: FastAPI response.
        tokens: Dict with access_token and refresh_token strings.
        secure: Whether to set the Secure flag.
    """
    missing_keys = _missing_auth_token_keys(tokens)
    if missing_keys:
        logger.error(
            "_set_auth_cookies: missing keys %s in tokens, got %s",
            missing_keys,
            list(tokens.keys()),
        )
        raise HTTPException(
            status_code=502,
            detail="Auth proxy returned incomplete token data",
        )
    _set_auth_cookie(
        response,
        key="access_token",
        value=tokens["access_token"],
        secure=secure,
    )
    _set_auth_cookie(
        response,
        key="refresh_token",
        value=tokens["refresh_token"],
        secure=secure,
    )
