"""Network worker tool implementations.

Executes web_search, http_fetch, and email_send within the
Net Worker subprocess. All URLs are validated for SSRF
prevention before requests are made.

Provides the subprocess entry point (net_worker_main) and the
Core-side dispatch handler factory (make_net_dispatch).
"""

from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura

from backend.security.content_filter import (
    filter_output,
)
from backend.tools.validation import (
    ValidationError,
    validate_url,
)


logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 30.0
MAX_RESPONSE_SIZE = 5_242_880  # 5 MB
MAX_REDIRECTS = 3
MAX_EXTRACTED_TEXT_LENGTH = 6000
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTTP_FETCH_USER_AGENT = "Mozilla/5.0 (compatible; biMEMO/1.0; +https://bimemo.app)"
_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

if TYPE_CHECKING:
    import multiprocessing.connection

    from backend.ipc.protocol import ToolRequest, ToolResponse


def _tool_success_response(request_id: str, result: dict) -> ToolResponse:
    """Build a standard successful worker response."""
    from backend.ipc.protocol import ToolResponse

    return ToolResponse(
        request_id=request_id,
        status="success",
        result=result,
    )


def _tool_error_response(request_id: str, message: str) -> ToolResponse:
    """Build a standard failed worker response."""
    from backend.ipc.protocol import ToolResponse

    return ToolResponse(
        request_id=request_id,
        status="error",
        error_message=message,
    )


def _unknown_tool_response(request_id: str, tool_name: str) -> ToolResponse:
    """Build a standard unknown-tool response."""
    return _tool_error_response(request_id, f"Unknown tool: {tool_name}")


def _email_send_result(parameters: dict) -> dict:
    """Run one email-send request from the raw worker parameter payload."""
    return execute_email_send(
        to=parameters["to"],
        subject=parameters["subject"],
        body=parameters["body"],
        smtp_host=parameters["smtp_host"],
        smtp_port=parameters["smtp_port"],
        smtp_user=parameters["smtp_user"],
        smtp_pass=parameters["smtp_pass"],
        from_addr=parameters["from_addr"],
    )


async def execute_web_search(query: str, searxng_url: str) -> dict:
    """Search via local SearXNG instance.

    Args:
        query: Search query string (max 200 chars).
        searxng_url: Full URL to the SearXNG search endpoint.

    Returns:
        Dict with formatted search results.
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            searxng_url,
            data={"q": query, "format": "json"},
        )
        resp.raise_for_status()

    data = resp.json()
    results = data.get("results", [])[:10]
    formatted = []
    for r in results:
        formatted.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
        )
    return {"results": formatted}


def _extract_html_content(
    raw_html: str,
    url: str,
) -> tuple[str, str]:
    """Extract title and clean text from HTML using trafilatura.

    Args:
        raw_html: Raw HTML response body.
        url: Source URL for extraction context.

    Returns:
        Tuple of (title, body_text).
    """
    metadata = trafilatura.extract_metadata(raw_html, default_url=url)
    title = str(metadata.title) if metadata and metadata.title else ""
    body = trafilatura.extract(raw_html, url=url) or ""
    if not body:
        body = _strip_html_fallback(raw_html)
    return title, body


def _strip_html_fallback(raw_html: str) -> str:
    """Remove HTML tags as fallback when trafilatura fails."""
    text = _HTML_TAG_PATTERN.sub("", raw_html)
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _truncate_content(text: str, limit: int) -> str:
    """Truncate text to limit with a marker if exceeded."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[content truncated]"


def _is_html_response(response: httpx.Response) -> bool:
    """Return whether one HTTP response contains HTML content."""
    content_type = response.headers.get("content-type", "")
    return any(ct in content_type for ct in _HTML_CONTENT_TYPES)


def _build_fetch_result(
    *,
    response: httpx.Response,
    url: str,
) -> dict:
    """Build the structured http_fetch result from one response."""
    if not _is_html_response(response):
        return {
            "title": "",
            "body": _truncate_content(
                response.text,
                MAX_EXTRACTED_TEXT_LENGTH,
            ),
            "url": url,
            "status": response.status_code,
        }
    title, body = _extract_html_content(response.text, url)
    return {
        "title": title,
        "body": _truncate_content(body, MAX_EXTRACTED_TEXT_LENGTH),
        "url": url,
        "status": response.status_code,
    }


async def execute_http_fetch(url: str, allowed_host: str | None = None) -> dict:
    """Fetch a URL with SSRF validation on every redirect.

    Manually follows redirects so each hop is re-validated
    through ``validate_url`` (scheme check, DNS resolution,
    IP blocklist).  Aborts after MAX_REDIRECTS hops.

    Args:
        url: HTTPS URL to fetch.
        allowed_host: When set, the initial URL and every redirect hop
            must stay on this host (domain-scoped grant containment).

    Returns:
        Dict with response body text.

    Raises:
        ValidationError: If any URL (initial or redirect)
            fails SSRF checks.
    """
    current_url = url
    if allowed_host and urlsplit(current_url).hostname != allowed_host:
        raise ValidationError("url is outside the granted domain")
    validate_url(current_url)

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": _HTTP_FETCH_USER_AGENT},
    ) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            resp = await client.get(current_url)

            if resp.status_code not in _REDIRECT_STATUSES:
                break

            location = resp.headers.get("location")
            if not location:
                break

            current_url = urljoin(current_url, location)
            if allowed_host and urlsplit(current_url).hostname != allowed_host:
                raise ValidationError("redirect left the granted domain")
            validate_url(current_url)
        else:
            raise ValidationError(f"Too many redirects (max {MAX_REDIRECTS})")

        resp.raise_for_status()

    if len(resp.content) > MAX_RESPONSE_SIZE:
        return {
            "error": (
                f"Response too large: "
                f"{len(resp.content)} "
                f"bytes (max {MAX_RESPONSE_SIZE})"
            )
        }

    return _build_fetch_result(response=resp, url=url)


def execute_email_send(
    to: str,
    subject: str,
    body: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_addr: str,
) -> dict:
    """Send an email via SMTP with TLS.

    Args:
        to: Recipient email address.
        subject: Email subject.
        body: Email body text.
        smtp_host: SMTP server hostname.
        smtp_port: SMTP server port.
        smtp_user: SMTP username.
        smtp_pass: SMTP password.
        from_addr: Sender email address.

    Returns:
        Dict with message ID confirmation.
    """
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    message_id = msg["Message-ID"] or "sent"
    return {"message_id": str(message_id)}


def net_worker_main(
    socket_path: str,
    conn: multiprocessing.connection.Connection,
) -> None:
    """Subprocess entry point for the Net Worker.

    Reads the shared secret from the parent process via pipe,
    then blocks until terminated by the parent.

    Args:
        socket_path: Unix socket path (unused, managed by Core).
        conn: Pipe connection for receiving the shared secret.
    """
    from backend.workers.common import worker_main

    worker_main(conn)


def make_net_dispatch(searxng_url: str) -> callable:
    """Create a dispatch handler for Net Worker tool requests.

    Returns an async handler that routes ToolRequest messages
    to the appropriate network operation handler.

    Args:
        searxng_url: Full URL to the SearXNG search endpoint.

    Returns:
        Async handler function for ToolRequest dispatch.
    """

    async def handler(request: ToolRequest) -> ToolResponse:
        """Dispatch a tool request to the appropriate handler.

        Args:
            request: Tool execution request.

        Returns:
            Tool execution response.
        """
        p = request.parameters
        try:
            if request.tool_name == "web_search":
                query_filter = filter_output(p["query"])
                if query_filter.credentials_found:
                    return _tool_error_response(
                        request.request_id,
                        "Query contains credentials",
                    )
                result = await execute_web_search(p["query"], searxng_url)
            elif request.tool_name == "http_fetch":
                result = await execute_http_fetch(
                    p["url"],
                    allowed_host=str(p.get("allowed_host", "") or "") or None,
                )
            elif request.tool_name == "email_send":
                result = _email_send_result(p)
            else:
                return _unknown_tool_response(
                    request.request_id,
                    request.tool_name,
                )
            return _tool_success_response(
                request.request_id,
                result,
            )
        except Exception as e:
            return _tool_error_response(request.request_id, str(e))

    return handler
