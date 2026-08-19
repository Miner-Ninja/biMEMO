"""Shared constructor for RFC 9457 problem+json error responses.

Every HTTP error produced by the FastAPI process — exception handlers
and raw-ASGI middleware alike — is served as ``application/problem+json``
with a stable type IRI of the form ``urn:bimemo:problem:<slug>``.

The ``detail`` field keeps the historical human-readable text so
existing clients keep working; ``title`` is a stable short summary
derived from the status code.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_NAMESPACE = "urn:bimemo:problem"

# --- Stable problem type slugs (URN namespace ``urn:bimemo:problem``) ---
SLUG_AUTHENTICATION_REQUIRED = "authentication-required"
SLUG_BAD_GATEWAY = "bad-gateway"
SLUG_CSRF_FAILED = "csrf-failed"
SLUG_FORBIDDEN = "forbidden"
SLUG_INTERNAL_ERROR = "internal-error"
SLUG_LOCKED = "locked"
SLUG_NOT_CONFIGURED = "not-configured"
SLUG_NOT_FOUND = "not-found"
SLUG_PAYLOAD_TOO_LARGE = "payload-too-large"
SLUG_RATE_LIMITED = "rate-limited"
SLUG_UPSTREAM_ERROR = "upstream-error"
SLUG_VALIDATION_ERROR = "validation-error"

_STATUS_SLUGS: dict[int, str] = {
    400: "bad-request",
    401: SLUG_AUTHENTICATION_REQUIRED,
    403: SLUG_FORBIDDEN,
    404: SLUG_NOT_FOUND,
    405: "method-not-allowed",
    409: "conflict",
    413: SLUG_PAYLOAD_TOO_LARGE,
    415: "unsupported-media-type",
    422: SLUG_VALIDATION_ERROR,
    429: SLUG_RATE_LIMITED,
    500: SLUG_INTERNAL_ERROR,
    501: "not-implemented",
    502: SLUG_BAD_GATEWAY,
    503: "service-unavailable",
    504: "gateway-timeout",
}

_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Validation Error",
    429: "Too Many Requests",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}

_DEFAULT_SLUG = "error"
_DEFAULT_TITLE = "Error"


def problem_type(slug: str) -> str:
    """Return the stable type IRI for one problem type slug.

    Args:
        slug: Stable problem type slug (see the SLUG_* constants).

    Returns:
        The ``urn:bimemo:problem:<slug>`` IRI.
    """
    return f"{PROBLEM_TYPE_NAMESPACE}:{slug}"


def slug_for_status(status: int) -> str:
    """Return the default problem type slug for one HTTP status code.

    Args:
        status: HTTP status code.

    Returns:
        The stable slug used when a raised error carries no explicit one.
    """
    return _STATUS_SLUGS.get(status, _DEFAULT_SLUG)


def _encode(body: dict[str, Any]) -> bytes:
    """Encode one problem body compactly as UTF-8 JSON."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_problem(
    *,
    status: int,
    type_slug: str,
    detail: str,
    title: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one RFC 9457 problem body as a JSON-serializable dict.

    Args:
        status: HTTP status code.
        type_slug: Stable problem type slug (see the SLUG_* constants).
        detail: Human-readable text for this occurrence.
        title: Optional stable short summary; defaults to the standard
            per-status title.
        errors: Optional field-error extension for validation errors.

    Returns:
        The problem body with type, title, status, detail and — when
        given — the errors extension.
    """
    body: dict[str, Any] = {
        "type": problem_type(type_slug),
        "title": title or _STATUS_TITLES.get(status, _DEFAULT_TITLE),
        "status": status,
        "detail": detail,
    }
    if errors is not None:
        body["errors"] = errors
    return body


def problem_body(
    *,
    status: int,
    type_slug: str,
    detail: str,
    title: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> bytes:
    """Build one encoded problem body for raw-ASGI middleware responses.

    Args:
        status: HTTP status code.
        type_slug: Stable problem type slug.
        detail: Human-readable text for this occurrence.
        title: Optional stable short summary.
        errors: Optional field-error extension.

    Returns:
        The UTF-8 encoded ``application/problem+json`` body.
    """
    return _encode(
        build_problem(
            status=status,
            type_slug=type_slug,
            detail=detail,
            title=title,
            errors=errors,
        )
    )


def problem_response(
    *,
    status: int,
    type_slug: str,
    detail: str,
    headers: dict[str, str] | None = None,
    title: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    """Build one problem+json response for exception handlers and routes.

    Args:
        status: HTTP status code.
        type_slug: Stable problem type slug.
        detail: Human-readable text for this occurrence.
        headers: Optional extra response headers (e.g. Retry-After),
            passed through unchanged.
        title: Optional stable short summary.
        errors: Optional field-error extension.

    Returns:
        A JSONResponse with the ``application/problem+json`` media type.
    """
    return JSONResponse(
        build_problem(
            status=status,
            type_slug=type_slug,
            detail=detail,
            title=title,
            errors=errors,
        ),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def typed_http_exception(
    *,
    slug: str,
    status_code: int,
    detail: str,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    """Build an HTTPException that carries an explicit problem type slug.

    Routes that must answer with a type other than the one derived from
    the status code (e.g. upstream-error for a 400) raise the result;
    the HTTPException handler picks the slug up from the exception.

    Args:
        slug: Stable problem type slug for this error.
        status_code: HTTP status code.
        detail: Human-readable text for this occurrence.
        headers: Optional extra response headers.

    Returns:
        The ready-to-raise HTTPException.
    """
    exc = HTTPException(status_code=status_code, detail=detail, headers=headers)
    exc.problem_slug = slug
    return exc


# --- Exception handlers (registered by ``register_problem_handlers``) ---


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Serve one HTTP exception as a problem+json response."""
    del request
    slug = getattr(exc, "problem_slug", None) or slug_for_status(exc.status_code)
    if isinstance(exc.detail, str):
        detail = exc.detail
    elif exc.detail is None:
        detail = _STATUS_TITLES.get(exc.status_code, _DEFAULT_TITLE)
    else:
        detail = str(exc.detail)
    return problem_response(
        status=exc.status_code,
        type_slug=slug,
        detail=detail,
        headers=exc.headers,
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    """Serve one request validation error as a problem+json response.

    The per-field error objects move into the ``errors`` extension so
    clients can branch on the type IRI instead of the raw list.
    """
    del request
    return problem_response(
        status=422,
        type_slug=SLUG_VALIDATION_ERROR,
        detail="Request validation failed",
        errors=jsonable_encoder(exc.errors()),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Serve one unhandled exception as a problem+json 500 response.

    Replaces the plain-text 500 previously sent by the server-error
    middleware; the exception still propagates to the server for logging.
    """
    del request
    del exc
    return problem_response(
        status=500,
        type_slug=SLUG_INTERNAL_ERROR,
        detail="Internal Server Error",
    )


def register_problem_handlers(application: FastAPI) -> None:
    """Register the RFC 9457 problem+json exception handlers on one app.

    The HTTPException and RequestValidationError handlers replace the
    default FastAPI JSON handlers; the Exception handler becomes the
    server-error handler, replacing the plain-text 500 response.
    """
    application.add_exception_handler(
        StarletteHTTPException,
        _http_exception_handler,
    )
    application.add_exception_handler(
        RequestValidationError,
        _validation_error_handler,
    )
    application.add_exception_handler(Exception, _unhandled_exception_handler)
