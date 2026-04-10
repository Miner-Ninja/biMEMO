"""Tests for Net Worker tool implementations.

Covers web_search, http_fetch, and email_send with
SSRF validation and mocked external services.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from backend.ipc.protocol import ToolRequest
from backend.security.content_filter import FilterResult
from backend.tools.validation import ValidationError
from backend.workers.net_worker import (
    _build_fetch_result,
    _extract_html_content,
    _is_html_response,
    _strip_html_fallback,
    _truncate_content,
    execute_email_send,
    execute_http_fetch,
    execute_web_search,
    make_net_dispatch,
)


class TestWebSearch:
    """Tests for execute_web_search."""

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self):
        """Verify search results are formatted correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "content": "An example site",
                },
                {
                    "title": "Test",
                    "url": "https://test.com",
                    "content": "Test page",
                },
            ]
        }

        with patch("backend.workers.net_worker.httpx.AsyncClient") as mock_client_cls:
            client_instance = MagicMock()
            client_instance.__aenter__ = lambda self: _async_return(client_instance)
            client_instance.__aexit__ = lambda self, *a: _async_return(None)
            client_instance.post = _make_async_return(mock_response)
            mock_client_cls.return_value = client_instance

            result = await execute_web_search(
                "test query",
                "http://searxng:8888/search",
            )

        assert len(result["results"]) == 2
        assert result["results"][0]["title"] == "Example"
        assert result["results"][0]["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_limits_to_ten_results(self):
        """Verify results capped at 10."""
        results = [
            {
                "title": f"R{i}",
                "url": f"https://r{i}.com",
                "content": f"Snippet {i}",
            }
            for i in range(20)
        ]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": results}

        with patch("backend.workers.net_worker.httpx.AsyncClient") as mock_client_cls:
            client_instance = MagicMock()
            client_instance.__aenter__ = lambda self: _async_return(client_instance)
            client_instance.__aexit__ = lambda self, *a: _async_return(None)
            client_instance.post = _make_async_return(mock_response)
            mock_client_cls.return_value = client_instance

            result = await execute_web_search(
                "many results",
                "http://searxng:8888/search",
            )

        assert len(result["results"]) == 10


class TestHttpFetch:
    """Tests for execute_http_fetch."""

    @pytest.mark.asyncio
    async def test_fetches_content(self):
        """Verify URL content is returned."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "<html>Hello</html>"
        mock_response.content = b"<html>Hello</html>"
        mock_response.status_code = 200

        with (
            patch(
                "backend.workers.net_worker.validate_url",
                return_value="93.184.216.34",
            ),
            patch("backend.workers.net_worker.httpx.AsyncClient") as mock_client_cls,
        ):
            client_instance = MagicMock()
            client_instance.__aenter__ = lambda self: _async_return(client_instance)
            client_instance.__aexit__ = lambda self, *a: _async_return(None)
            client_instance.get = _make_async_return(mock_response)
            mock_client_cls.return_value = client_instance

            result = await execute_http_fetch("https://example.com")

        assert "Hello" in result["body"]
        assert result["status"] == 200

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        """Verify SSRF validation rejects private IPs."""
        with (
            patch(
                "backend.workers.net_worker.validate_url",
                side_effect=ValidationError(
                    "Resolved IP 127.0.0.1 is in blocked range"
                ),
            ),
            pytest.raises(ValidationError, match="blocked"),
        ):
            await execute_http_fetch("https://localhost/secret")

    @pytest.mark.asyncio
    async def test_large_response_error(self):
        """Verify error for responses over 5MB."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.content = b"x" * 5_242_881
        mock_response.text = "x" * 5_242_881
        mock_response.status_code = 200

        with (
            patch(
                "backend.workers.net_worker.validate_url",
                return_value="1.2.3.4",
            ),
            patch("backend.workers.net_worker.httpx.AsyncClient") as mock_client_cls,
        ):
            client_instance = MagicMock()
            client_instance.__aenter__ = lambda self: _async_return(client_instance)
            client_instance.__aexit__ = lambda self, *a: _async_return(None)
            client_instance.get = _make_async_return(mock_response)
            mock_client_cls.return_value = client_instance

            result = await execute_http_fetch("https://large.example.com")

        assert "error" in result
        assert "too large" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_safe_redirect_followed(self):
        """Verify a safe redirect is followed successfully."""
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {
            "location": "https://safe.example.com/page",
        }
        redirect_resp.raise_for_status = MagicMock()

        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.raise_for_status = MagicMock()
        final_resp.text = "<html>Final</html>"
        final_resp.content = b"<html>Final</html>"

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return redirect_resp
            return final_resp

        with (
            patch(
                "backend.workers.net_worker.validate_url",
                return_value="1.2.3.4",
            ) as mock_validate,
            patch(
                "backend.workers.net_worker.httpx.AsyncClient",
            ) as mock_client_cls,
        ):
            client = MagicMock()
            client.__aenter__ = lambda self: _async_return(client)
            client.__aexit__ = lambda self, *a: _async_return(None)
            client.get = mock_get
            mock_client_cls.return_value = client

            result = await execute_http_fetch("https://example.com")

        assert result["body"] == "<html>Final</html>"
        assert result["status"] == 200
        assert mock_validate.call_count == 2
        mock_validate.assert_any_call("https://example.com")
        mock_validate.assert_any_call("https://safe.example.com/page")

    @pytest.mark.asyncio
    async def test_redirect_to_private_ip_blocked(self):
        """Verify redirect to a private IP is blocked."""
        redirect_resp = MagicMock()
        redirect_resp.status_code = 301
        redirect_resp.headers = {
            "location": "https://internal.corp/secret",
        }

        def validate_side_effect(url):
            if "internal.corp" in url:
                raise ValidationError(
                    "Resolved IP 10.0.0.1 is in blocked range 10.0.0.0/8"
                )
            return "93.184.216.34"

        with (
            patch(
                "backend.workers.net_worker.validate_url",
                side_effect=validate_side_effect,
            ),
            patch(
                "backend.workers.net_worker.httpx.AsyncClient",
            ) as mock_client_cls,
        ):
            client = MagicMock()
            client.__aenter__ = lambda self: _async_return(client)
            client.__aexit__ = lambda self, *a: _async_return(None)
            client.get = _make_async_return(redirect_resp)
            mock_client_cls.return_value = client

            with pytest.raises(ValidationError, match="blocked"):
                await execute_http_fetch("https://example.com")

    @pytest.mark.asyncio
    async def test_non_https_redirect_blocked(self):
        """Verify redirect to HTTP (non-HTTPS) is blocked."""
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {
            "location": "http://example.com/plain",
        }

        def validate_side_effect(url):
            if url.startswith("http://"):
                raise ValidationError("URL scheme not allowed: http")
            return "93.184.216.34"

        with (
            patch(
                "backend.workers.net_worker.validate_url",
                side_effect=validate_side_effect,
            ),
            patch(
                "backend.workers.net_worker.httpx.AsyncClient",
            ) as mock_client_cls,
        ):
            client = MagicMock()
            client.__aenter__ = lambda self: _async_return(client)
            client.__aexit__ = lambda self, *a: _async_return(None)
            client.get = _make_async_return(redirect_resp)
            mock_client_cls.return_value = client

            with pytest.raises(
                ValidationError,
                match="scheme not allowed",
            ):
                await execute_http_fetch("https://example.com")

    @pytest.mark.asyncio
    async def test_max_redirects_exceeded(self):
        """Verify error when redirect count exceeds limit."""
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.raise_for_status = MagicMock()

        hop_counter = 0

        async def mock_get(*args, **kwargs):
            nonlocal hop_counter
            hop_counter += 1
            redirect_resp.headers = {
                "location": (f"https://hop{hop_counter}.example.com"),
            }
            return redirect_resp

        with (
            patch(
                "backend.workers.net_worker.validate_url",
                return_value="1.2.3.4",
            ),
            patch(
                "backend.workers.net_worker.httpx.AsyncClient",
            ) as mock_client_cls,
        ):
            client = MagicMock()
            client.__aenter__ = lambda self: _async_return(client)
            client.__aexit__ = lambda self, *a: _async_return(None)
            client.get = mock_get
            mock_client_cls.return_value = client

            with pytest.raises(
                ValidationError,
                match="Too many redirects",
            ):
                await execute_http_fetch("https://example.com")


class TestEmailSend:
    """Tests for execute_email_send."""

    def test_sends_email(self):
        """Verify email is sent via SMTP."""
        with patch("backend.workers.net_worker.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = lambda self: mock_server
            mock_smtp_cls.return_value.__exit__ = lambda self, *a: None

            result = execute_email_send(
                to="user@example.com",
                subject="Test",
                body="Hello",
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user="user",
                smtp_pass="pass",
                from_addr="bot@example.com",
            )

        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pass")
        mock_server.send_message.assert_called_once()
        assert "message_id" in result

    def test_email_fields_correct(self):
        """Verify email message fields are set."""
        with patch("backend.workers.net_worker.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = lambda self: mock_server
            mock_smtp_cls.return_value.__exit__ = lambda self, *a: None

            execute_email_send(
                to="dest@test.com",
                subject="Important",
                body="Message body",
                smtp_host="smtp.test.com",
                smtp_port=465,
                smtp_user="admin",
                smtp_pass="secret",
                from_addr="sender@test.com",
            )

        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg["To"] == "dest@test.com"
        assert sent_msg["Subject"] == "Important"
        assert sent_msg["From"] == "sender@test.com"


class TestNetDispatchCredentialFilter:
    """Tests for credential filtering in make_net_dispatch."""

    @pytest.mark.asyncio
    async def test_clean_query_passes_through(self):
        """Verify clean query reaches execute_web_search."""
        clean_result = FilterResult(
            text="python tutorials",
            credentials_found=[],
        )
        search_result = {"results": []}

        with (
            patch(
                "backend.workers.net_worker.filter_output",
                return_value=clean_result,
            ) as mock_filter,
            patch(
                "backend.workers.net_worker.execute_web_search",
                new_callable=AsyncMock,
                return_value=search_result,
            ) as mock_search,
        ):
            handler = make_net_dispatch("http://searxng:8888/search")
            request = ToolRequest(
                request_id="req-1",
                tool_name="web_search",
                parameters={"query": "python tutorials"},
            )
            response = await handler(request)

        mock_filter.assert_called_once_with("python tutorials")
        mock_search.assert_called_once_with(
            "python tutorials",
            "http://searxng:8888/search",
        )
        assert response.status == "success"
        assert response.result == search_result

    @pytest.mark.asyncio
    async def test_credential_query_rejected(self):
        """Verify query with credentials is rejected."""
        cred_result = FilterResult(
            text="[REDACTED]",
            credentials_found=["OpenAI API key"],
        )

        with (
            patch(
                "backend.workers.net_worker.filter_output",
                return_value=cred_result,
            ) as mock_filter,
            patch(
                "backend.workers.net_worker.execute_web_search",
                new_callable=AsyncMock,
            ) as mock_search,
        ):
            handler = make_net_dispatch("http://searxng:8888/search")
            request = ToolRequest(
                request_id="req-2",
                tool_name="web_search",
                parameters={"query": "sk-abc12345678901234567"},
            )
            response = await handler(request)

        mock_filter.assert_called_once_with("sk-abc12345678901234567")
        mock_search.assert_not_called()
        assert response.status == "error"
        assert response.error_message == ("Query contains credentials")


class TestHttpFetchContentExtraction:
    """Tests for HTTP fetch content extraction helpers."""

    def test_extract_html_returns_clean_text(self):
        """Verify extracted body contains no HTML tags."""
        raw_html = (
            "<html><head><title>Test Page</title></head>"
            "<body><article><p>This is the main content.</p>"
            "<p>Second paragraph here.</p></article></body></html>"
        )
        _title, body = _extract_html_content(raw_html, "https://example.com")

        assert "<" not in body
        assert ">" not in body
        assert "content" in body.lower() or "paragraph" in body.lower()

    def test_extract_html_includes_title(self):
        """Verify title is extracted from HTML metadata."""
        raw_html = (
            "<html><head><title>My Page Title</title></head>"
            "<body><article><p>Body text.</p></article></body></html>"
        )
        title, _body = _extract_html_content(raw_html, "https://example.com")

        assert title == "My Page Title"

    def test_strip_html_fallback_removes_tags(self):
        """Verify fallback strips all HTML tags from input."""
        raw_html = "<p>Hello <b>world</b></p><br/><div>End</div>"
        result = _strip_html_fallback(raw_html)

        assert "<" not in result
        assert ">" not in result
        assert "Hello" in result
        assert "world" in result
        assert "End" in result

    def test_truncate_within_limit(self):
        """Verify text shorter than limit is returned unchanged."""
        text = "Short text"
        result = _truncate_content(text, 100)

        assert result == text

    def test_truncate_exceeds_limit(self):
        """Verify text exceeding limit is truncated with marker."""
        text = "A" * 200
        limit = 50
        result = _truncate_content(text, limit)

        assert result.endswith("[content truncated]")
        assert result.startswith("A" * limit)
        assert len(result) == limit + len("\n\n[content truncated]")

    def test_is_html_response_true(self):
        """Verify HTML content-type is detected as HTML."""
        response = httpx.Response(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html></html>",
        )
        assert _is_html_response(response) is True

    def test_is_html_response_false_json(self):
        """Verify JSON content-type is not detected as HTML."""
        response = httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            text='{"key": "value"}',
        )
        assert _is_html_response(response) is False

    def test_build_fetch_result_html(self):
        """Verify HTML response produces structured dict with extracted content."""
        html = (
            "<html><head><title>Article Title</title></head>"
            "<body><article><p>Article body content.</p></article></body></html>"
        )
        response = httpx.Response(
            status_code=200,
            headers={"content-type": "text/html"},
            text=html,
        )
        result = _build_fetch_result(response=response, url="https://example.com")

        assert result["url"] == "https://example.com"
        assert result["status"] == 200
        assert result["title"] == "Article Title"
        assert "<" not in result["body"]

    def test_build_fetch_result_json(self):
        """Verify non-HTML response returns body as-is with empty title."""
        json_text = '{"data": "value"}'
        response = httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            text=json_text,
        )
        result = _build_fetch_result(response=response, url="https://api.example.com")

        assert result["title"] == ""
        assert result["body"] == json_text
        assert result["url"] == "https://api.example.com"
        assert result["status"] == 200


async def _async_return(value):
    """Helper to create an awaitable return value."""
    return value


def _make_async_return(value):
    """Create an async function that returns a value."""

    async def _fn(*args, **kwargs):
        return value

    return _fn
