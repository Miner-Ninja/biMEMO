"""HTTP client helpers for the memory loop test harness."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


@dataclass(slots=True)
class APIClient:
    """Minimal cookie-aware API client built on the Python stdlib."""

    base_url: str
    auth_password: str = ""
    cookies: CookieJar = field(default_factory=CookieJar)
    csrf_token: str = ""
    _opener: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Create a reusable opener with cookie support."""
        self.base_url = self.base_url.rstrip("/")
        parsed_base_url = urlsplit(self.base_url)
        if parsed_base_url.scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        self._opener = build_opener(HTTPCookieProcessor(self.cookies))

    def __init__(self, base_url: str, auth_password: str = "") -> None:
        """Initialize the HTTP client slots manually."""
        self.base_url = base_url
        self.auth_password = auth_password
        self.cookies = CookieJar()
        self.csrf_token = ""
        self.__post_init__()

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Send a GET request and return JSON data."""
        return self._request("GET", path, body=None, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Send a POST request and return JSON data."""
        return self._request("POST", path, body=body, params=None, timeout=timeout)

    def delete(
        self,
        path: str,
        timeout: int = 30,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send a DELETE request and return JSON data."""
        return self._request(
            "DELETE",
            path,
            body=None,
            params=None,
            timeout=timeout,
            extra_headers=extra_headers,
        )

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        params: dict[str, Any] | None,
        timeout: int,
        extra_headers: dict[str, str] | None = None,
        allow_reauth: bool = True,
        retry_count: int = 0,
    ) -> dict[str, Any]:
        """Perform a JSON HTTP request."""
        raise NotImplementedError(
            "Use request_json() from memory_loop_api with explicit policy inputs."
        )


def request_json(
    client: APIClient,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None,
    params: dict[str, Any] | None,
    timeout: int,
    auth_exempt_paths: set[str],
    request_retry_limit: int,
    rate_limit_backoff_seconds: float,
    extra_headers: dict[str, str] | None = None,
    allow_reauth: bool = True,
    retry_count: int = 0,
    authenticate_fn: Any = None,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    """Perform a JSON HTTP request with explicit retry and re-auth policy."""
    url = f"{client.base_url}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http and https URLs are allowed")

    data = None
    if body is not None:
        data = json.dumps(body).encode()

    request = Request(url, data=data, method=method)  # noqa: S310
    request.add_header("Content-Type", "application/json")
    if client.csrf_token:
        request.add_header("X-CSRF-Token", client.csrf_token)
    if extra_headers:
        for header_name, header_value in extra_headers.items():
            request.add_header(header_name, header_value)

    try:
        with client._opener.open(request, timeout=timeout) as response:
            raw_body = response.read().decode()
    except HTTPError as error:
        if (
            error.code == 401
            and allow_reauth
            and client.auth_password
            and path not in auth_exempt_paths
            and authenticate_fn is not None
        ):
            authenticate_fn(client, client.auth_password)
            return request_json(
                client,
                method,
                path,
                body=body,
                params=params,
                timeout=timeout,
                auth_exempt_paths=auth_exempt_paths,
                request_retry_limit=request_retry_limit,
                rate_limit_backoff_seconds=rate_limit_backoff_seconds,
                extra_headers=extra_headers,
                allow_reauth=False,
                retry_count=retry_count,
                authenticate_fn=authenticate_fn,
            )
        if error.code == 429 and retry_count < request_retry_limit:
            sleep_fn(rate_limit_backoff_seconds)
            return request_json(
                client,
                method,
                path,
                body=body,
                params=params,
                timeout=timeout,
                auth_exempt_paths=auth_exempt_paths,
                request_retry_limit=request_retry_limit,
                rate_limit_backoff_seconds=rate_limit_backoff_seconds,
                extra_headers=extra_headers,
                allow_reauth=allow_reauth,
                retry_count=retry_count + 1,
                authenticate_fn=authenticate_fn,
                sleep_fn=sleep_fn,
            )
        raw_body = error.read().decode()
        raise RuntimeError(f"{method} {path} -> {error.code}: {raw_body}") from error

    if not raw_body.strip():
        return {}
    return json.loads(raw_body)


def authenticate_client(
    client: APIClient,
    password: str,
    *,
    get_timeout: int,
    chat_timeout: int,
    auth_exempt_paths: set[str],
    request_retry_limit: int,
    rate_limit_backoff_seconds: float,
) -> None:
    """Authenticate against biMEMO and cache the CSRF token."""
    client.auth_password = password
    status = request_json(
        client,
        "GET",
        "/auth/status",
        body=None,
        params=None,
        timeout=get_timeout,
        auth_exempt_paths=auth_exempt_paths,
        request_retry_limit=request_retry_limit,
        rate_limit_backoff_seconds=rate_limit_backoff_seconds,
    )
    if status.get("needs_setup"):
        request_json(
            client,
            "POST",
            "/auth/setup",
            body={"password": password},
            params=None,
            timeout=chat_timeout,
            auth_exempt_paths=auth_exempt_paths,
            request_retry_limit=request_retry_limit,
            rate_limit_backoff_seconds=rate_limit_backoff_seconds,
        )
    else:
        request_json(
            client,
            "POST",
            "/auth/login",
            body={"password": password},
            params=None,
            timeout=chat_timeout,
            auth_exempt_paths=auth_exempt_paths,
            request_retry_limit=request_retry_limit,
            rate_limit_backoff_seconds=rate_limit_backoff_seconds,
        )
    csrf_payload = request_json(
        client,
        "GET",
        "/csrf-token",
        body=None,
        params=None,
        timeout=get_timeout,
        auth_exempt_paths=auth_exempt_paths,
        request_retry_limit=request_retry_limit,
        rate_limit_backoff_seconds=rate_limit_backoff_seconds,
    )
    token = str(csrf_payload.get("token", ""))
    if not token:
        raise RuntimeError("Failed to obtain a CSRF token from /csrf-token")
    client.csrf_token = token


def authenticate_with_runtime_fallback_impl(
    base_url: str,
    password: str,
    *,
    client_factory: Any,
    authenticate_fn: Any,
    default_base_url: str,
    container_local_base_url: str,
) -> APIClient:
    """Authenticate and fall back to the container-local API when needed."""
    client = client_factory(base_url)
    try:
        authenticate_fn(client, password)
        return client
    except Exception as error:
        if base_url != default_base_url:
            raise
        error_text = str(error)
        if "Connection refused" not in error_text and "Errno 111" not in error_text:
            raise

    fallback_client = client_factory(container_local_base_url)
    authenticate_fn(fallback_client, password)
    print(
        f"Falling back to container-local API base URL {container_local_base_url}",
        flush=True,
    )
    return fallback_client


def is_rate_limit_error(error: Exception) -> bool:
    """Return whether ``error`` represents a chat rate-limit response."""
    return "429" in str(error)
