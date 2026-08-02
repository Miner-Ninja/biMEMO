"""Authentication routes."""

# ruff: noqa: B008

from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.channels.jwt_auth import (
    SessionManager,
    TokenClaims,
    generate_csrf_token,
)
from backend.models.requests import PasswordAuthRequest  # noqa: TC001

from ..deps import (
    _get_audit_fn,
    _get_session_manager,
    _get_signing_client,
    _require_auth,
)
from ..middleware import _cookie_secure, _set_auth_cookies


if TYPE_CHECKING:
    from ..clients import SigningClient


REFRESH_TOKEN_TYPE = "refresh"  # noqa: S105

auth_router = APIRouter()


def _status_ok_response() -> dict[str, str]:
    """Build one simple ok status response."""
    return {"status": "ok"}


def _detail_response(detail: str) -> dict[str, str]:
    """Build one simple detail response."""
    return {"detail": detail}


def _extract_proxy_error(detail: object) -> tuple[str, int | None]:
    """Unpack proxy error detail and optional retry-after seconds."""
    if isinstance(detail, dict):
        error = str(detail.get("error", ""))
        retry_after = detail.get("retry_after")
        if isinstance(retry_after, (int, float)):
            return error, int(retry_after)
        return error, None
    return str(detail), None


def _extract_refresh_token(request: Request) -> str | None:
    """Extract refresh token from request cookies."""
    cookie_header = request.headers.get("cookie", "")
    if not cookie_header:
        return None
    cookie = SimpleCookie(cookie_header)
    morsel = cookie.get("refresh_token")
    if morsel is None:
        return None
    return morsel.value


def _invalid_refresh_token_error() -> HTTPException:
    """Build the shared invalid-refresh-token HTTP error."""
    return HTTPException(
        status_code=401,
        detail="Invalid refresh token",
    )


def _refresh_claims(
    claims_data: dict[str, object],
) -> TokenClaims:
    """Build validated refresh-token claims from verified token data."""
    return TokenClaims(
        jti=claims_data["jti"],
        iat=claims_data["iat"],
        exp=claims_data["exp"],
        generation=claims_data["gen"],
        token_type=claims_data["type"],
    )


async def _audit_auth_event(
    audit_fn: object,
    *,
    event_type: str,
    tool_name: str,
) -> None:
    """Write one auth audit event when auditing is enabled."""
    if audit_fn is not None:
        await audit_fn(event_type, tool_name=tool_name)


async def _audit_auth_failure(audit_fn: object, tool_name: str) -> None:
    """Write one standard auth failure audit event."""
    await _audit_auth_event(
        audit_fn,
        event_type="auth_failure",
        tool_name=tool_name,
    )


def _complete_auth(response: Response, request: Request, tokens: dict) -> dict:
    """Set cookies and return the standard success body."""
    _set_auth_cookies(response, tokens, _cookie_secure(request))
    return _status_ok_response()


def _clear_auth_cookies(response: Response) -> None:
    """Remove both auth cookies from one response."""
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


@auth_router.get("/auth/status")
async def auth_status(
    request: Request,
    signing_client: SigningClient = Depends(_get_signing_client),
) -> dict:
    """Check authentication state: needs_setup and authenticated.

    Args:
        request: FastAPI request.
        signing_client: Signing Proxy client.

    Returns:
        Dict with needs_setup and authenticated booleans.
    """
    result = await signing_client.get_auth_status()
    needs_setup = result.get("needs_setup", True)
    claims = getattr(request.state, "claims", None)
    authenticated = claims is not None
    return {"needs_setup": needs_setup, "authenticated": authenticated}


@auth_router.post("/auth/setup")
async def auth_setup(
    body: PasswordAuthRequest,
    request: Request,
    response: Response,
    signing_client: SigningClient = Depends(_get_signing_client),
    audit_fn: object = Depends(_get_audit_fn),
) -> dict:
    """Create the user password on first launch.

    Args:
        body: Request body containing password.
        request: FastAPI request.
        response: FastAPI response for cookies.
        signing_client: Signing Proxy client.
        audit_fn: Audit event callable.

    Returns:
        Success status.
    """
    try:
        tokens = await signing_client.setup_password(body.password)
    except HTTPException as exc:
        if "password_already_exists" in str(exc.detail):
            await _audit_auth_failure(audit_fn, "setup")
            raise HTTPException(
                status_code=409,
                detail="Password already configured",
            ) from None
        await _audit_auth_failure(audit_fn, "setup")
        raise
    await _audit_auth_event(audit_fn, event_type="auth_success", tool_name="setup")
    return _complete_auth(response, request, tokens)


@auth_router.post("/auth/login")
async def auth_login(
    body: PasswordAuthRequest,
    request: Request,
    response: Response,
    signing_client: SigningClient = Depends(_get_signing_client),
    audit_fn: object = Depends(_get_audit_fn),
) -> dict:
    """Authenticate with password.

    Args:
        body: Request body containing password.
        request: FastAPI request.
        response: FastAPI response for cookies.
        signing_client: Signing Proxy client.
        audit_fn: Audit event callable.

    Returns:
        Success status.
    """
    try:
        tokens = await signing_client.verify_password(body.password)
    except HTTPException as exc:
        detail, retry_after = _extract_proxy_error(exc.detail)
        if "rate_limited" in detail:
            await _audit_auth_failure(audit_fn, "login_rate_limited")
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts",
                headers={"Retry-After": str(retry_after or 30)},
            ) from None
        if "invalid_password" in detail:
            await _audit_auth_failure(audit_fn, "login")
            raise HTTPException(
                status_code=401,
                detail="Invalid password",
            ) from None
        await _audit_auth_failure(audit_fn, "login")
        raise
    await _audit_auth_event(audit_fn, event_type="auth_success", tool_name="login")
    return _complete_auth(response, request, tokens)


@auth_router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    session_mgr: SessionManager = Depends(_get_session_manager),
    signing_client: SigningClient = Depends(_get_signing_client),
) -> dict:
    """Refresh access token using refresh token.

    Args:
        request: FastAPI request.
        response: FastAPI response for cookies.
        session_mgr: Session manager.
        signing_client: Signing Proxy client.

    Returns:
        Success message.
    """
    refresh_token = _extract_refresh_token(request)
    if refresh_token is None:
        raise HTTPException(
            status_code=401,
            detail="Refresh token required",
        )

    claims_data = await signing_client.verify_token(refresh_token)
    if claims_data is None:
        raise _invalid_refresh_token_error()
    try:
        claims = _refresh_claims(claims_data)
    except (KeyError, TypeError) as exc:
        raise _invalid_refresh_token_error() from exc

    if claims.token_type != REFRESH_TOKEN_TYPE:
        raise _invalid_refresh_token_error()
    validated_claims = await session_mgr.async_validate_token(
        refresh_token,
        signing_client,
        expected_type=REFRESH_TOKEN_TYPE,
    )
    if validated_claims is None:
        raise _invalid_refresh_token_error()

    session_mgr.revoke_token(validated_claims.jti, validated_claims.exp)
    new_access = await session_mgr.async_issue_access_token(signing_client)
    new_refresh = await session_mgr.async_issue_refresh_token(signing_client)
    _set_auth_cookies(
        response,
        {
            "access_token": new_access,
            "refresh_token": new_refresh,
        },
        _cookie_secure(request),
    )
    return _detail_response("Token refreshed")


@auth_router.post("/logout")
async def logout(
    response: Response,
    claims: TokenClaims = Depends(_require_auth),
    session_mgr: SessionManager = Depends(_get_session_manager),
) -> dict:
    """Logout and revoke tokens.

    Args:
        response: FastAPI response for clearing cookies.
        claims: Authenticated token claims.
        session_mgr: Session manager.

    Returns:
        Success message.
    """
    session_mgr.revoke_token(claims.jti, claims.exp)
    _clear_auth_cookies(response)
    return _detail_response("Logged out")


@auth_router.get("/csrf-token")
async def csrf_token(
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
) -> dict:
    """Issue a CSRF token for the authenticated session.

    Args:
        request: FastAPI request.
        claims: Authenticated token claims.

    Returns:
        CSRF token string.
    """
    token = generate_csrf_token()
    request.app.state.csrf_tokens[claims.jti] = token
    return {"token": token}
