#!/usr/bin/env python3
"""Smoke-test `/chat` start acknowledgement plus `/ws` final path."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import socket
import ssl
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.memory_loop_api import APIClient, authenticate_client, request_json
from scripts.memory_loop_constants import (
    AUTH_EXEMPT_PATHS,
    DEFAULT_BASE_URL,
    DEFAULT_CHAT_TIMEOUT_SECONDS,
    DEFAULT_GET_TIMEOUT_SECONDS,
    RATE_LIMIT_BACKOFF_SECONDS,
    REQUEST_RETRY_LIMIT,
)


DEFAULT_STREAM_TIMEOUT_SECONDS = 90
DEFAULT_MESSAGE = "Smoke test over WebSocket: answer with one short sentence."
OK_SYMBOL = "✅"
FAIL_SYMBOL = "❌"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_TEXT = 0x1
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


@dataclass(slots=True)
class Check:
    """One smoke-test checklist item."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class WebSocketResult:
    """Relevant terminal events observed for one chat request."""

    events: list[dict[str, Any]]
    final_event: dict[str, Any] | None = None
    error_event: dict[str, Any] | None = None
    done_event: dict[str, Any] | None = None
    stream_exception: str = ""


class MinimalWebSocket:
    """Small stdlib WebSocket client for smoke tests."""

    def __init__(self, url: str, cookie_header: str) -> None:
        """Initialize one client for a ws/wss URL."""
        self.url = url
        self.cookie_header = cookie_header
        self.sock: socket.socket | ssl.SSLSocket | None = None

    def connect(self, timeout: int) -> None:
        """Open the socket and perform a WebSocket handshake."""
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"ws", "wss"}:
            raise ValueError("WebSocket URL must use ws or wss")
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw_sock = socket.create_connection((host, port), timeout=timeout)
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            sock: socket.socket | ssl.SSLSocket = context.wrap_socket(
                raw_sock,
                server_hostname=host,
            )
        else:
            sock = raw_sock
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if self.cookie_header:
            headers.append(f"Cookie: {self.cookie_header}")
        request = "\r\n".join(headers) + "\r\n\r\n"
        sock.sendall(request.encode("ascii"))
        response = self._read_http_response(sock)
        accept = base64.b64encode(
            hashlib.sha1(  # noqa: S324 - required by RFC 6455 handshake.
                f"{key}{WS_GUID}".encode("ascii")
            ).digest(),
        ).decode()
        if " 101 " not in response.split("\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket handshake failed: {response[:200]}")
        if f"sec-websocket-accept: {accept.lower()}" not in response.lower():
            raise RuntimeError("WebSocket handshake accept header mismatch")
        self.sock = sock

    def recv_json(self) -> dict[str, Any]:
        """Read one text frame and decode it as JSON."""
        while True:
            opcode, payload = self._recv_frame()
            if opcode == OPCODE_TEXT:
                return json.loads(payload.decode("utf-8"))
            if opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_CLOSE:
                raise RuntimeError("WebSocket closed")

    def close(self) -> None:
        """Close the socket."""
        if self.sock is None:
            return
        with suppress(OSError):
            self._send_frame(OPCODE_CLOSE, b"")
        self.sock.close()
        self.sock = None

    def _read_http_response(self, sock: socket.socket | ssl.SSLSocket) -> str:
        """Read an HTTP response header block."""
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            data = b"".join(chunks)
            if b"\r\n\r\n" in data:
                return data.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
        raise RuntimeError("WebSocket handshake response was empty")

    def _recv_exact(self, size: int) -> bytes:
        """Read exactly `size` bytes from the socket."""
        if self.sock is None:
            raise RuntimeError("WebSocket is not connected")
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RuntimeError("WebSocket closed while reading frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_frame(self) -> tuple[int, bytes]:
        """Read one server-to-client WebSocket frame."""
        header = self._recv_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        payload_len = header[1] & 0x7F
        if payload_len == 126:
            payload_len = int.from_bytes(self._recv_exact(2), "big")
        elif payload_len == 127:
            payload_len = int.from_bytes(self._recv_exact(8), "big")
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(payload_len)
        if masked:
            payload = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
        return opcode, payload

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        """Send one masked client-to-server WebSocket frame."""
        if self.sock is None:
            return
        mask = os.urandom(4)
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = bytes([first, 0x80 | length])
        elif length < 65536:
            header = bytes([first, 0x80 | 126]) + length.to_bytes(2, "big")
        else:
            header = bytes([first, 0x80 | 127]) + length.to_bytes(8, "big")
        masked_payload = bytes(
            byte ^ mask[index % 4] for index, byte in enumerate(payload)
        )
        self.sock.sendall(header + mask + masked_payload)


def authenticate(client: APIClient, password: str) -> None:
    """Authenticate and cache cookies plus CSRF token."""
    authenticate_client(
        client,
        password,
        get_timeout=DEFAULT_GET_TIMEOUT_SECONDS,
        chat_timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
    )


def api_request(
    client: APIClient,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_GET_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send a JSON API request through the shared stdlib client."""
    return request_json(
        client,
        method,
        path,
        body=body,
        params=params,
        timeout=timeout,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        authenticate_fn=authenticate,
    )


def build_client(base_url: str, password: str) -> APIClient:
    """Build and authenticate one API client."""
    client = APIClient(base_url)
    authenticate(client, password)
    return client


def cookie_header(client: APIClient) -> str:
    """Build a Cookie header from the API client's cookie jar."""
    cookies: list[str] = []
    for cookie in client.cookies:
        typed_cookie = cookie
        if isinstance(typed_cookie, Cookie):
            cookies.append(f"{typed_cookie.name}={typed_cookie.value}")
    return "; ".join(cookies)


def websocket_url(base_url: str, conversation_id: str) -> str:
    """Build a WebSocket URL from the HTTP base URL."""
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc
    params = urlencode({"conversation_id": conversation_id})
    return f"{scheme}://{netloc}/ws?{params}"


def open_websocket_reader(
    *,
    url: str,
    cookies: str,
    output_queue: queue.Queue[dict[str, Any]],
    ready_event: threading.Event,
    stop_event: threading.Event,
    timeout: int,
) -> threading.Thread:
    """Start a background reader for one WebSocket connection."""
    thread = threading.Thread(
        target=_read_websocket_events,
        kwargs={
            "url": url,
            "cookies": cookies,
            "output_queue": output_queue,
            "ready_event": ready_event,
            "stop_event": stop_event,
            "timeout": timeout,
        },
        daemon=True,
    )
    thread.start()
    return thread


def _read_websocket_events(
    *,
    url: str,
    cookies: str,
    output_queue: queue.Queue[dict[str, Any]],
    ready_event: threading.Event,
    stop_event: threading.Event,
    timeout: int,
) -> None:
    """Read WebSocket messages and put decoded payloads on a queue."""
    websocket = MinimalWebSocket(url, cookies)
    try:
        websocket.connect(timeout)
        ready_event.set()
        while not stop_event.is_set():
            output_queue.put(websocket.recv_json())
    except Exception as error:
        ready_event.set()
        output_queue.put({"type": "stream_exception", "detail": str(error)})
    finally:
        websocket.close()


def wait_for_chat_terminal_events(
    *,
    events: queue.Queue[dict[str, Any]],
    request_id: str,
    timeout: int,
) -> WebSocketResult:
    """Wait until WebSocket emits final/error and done for one request."""
    deadline = time.monotonic() + timeout
    observed: list[dict[str, Any]] = []
    final_event: dict[str, Any] | None = None
    error_event: dict[str, Any] | None = None
    done_event: dict[str, Any] | None = None
    stream_exception = ""

    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            event = events.get(timeout=min(1.0, remaining))
        except queue.Empty:
            continue
        observed.append(event)
        event_type = str(event.get("type", ""))
        if event_type == "stream_exception":
            stream_exception = str(event.get("detail", ""))
            break
        if event.get("request_id") != request_id:
            continue
        if event_type == "final":
            final_event = event
        elif event_type == "error":
            error_event = event
        elif event_type == "done":
            done_event = event
        has_terminal_payload = final_event is not None or error_event is not None
        if done_event is not None and has_terminal_payload:
            break

    return WebSocketResult(
        events=observed,
        final_event=final_event,
        error_event=error_event,
        done_event=done_event,
        stream_exception=stream_exception,
    )


def run_smoke(
    *,
    client: APIClient,
    message: str,
    stream_timeout: int,
    keep_conversation: bool,
) -> list[Check]:
    """Run the live WebSocket chat stream smoke test."""
    checks: list[Check] = []
    health = api_request(client, "GET", "/health")
    checks.append(Check("API health", health.get("status") in {"healthy", "ok"}))

    auth_status = api_request(client, "GET", "/auth/status")
    checks.append(Check("Auth setup/login", bool(auth_status.get("authenticated"))))

    conversation = api_request(
        client,
        "POST",
        "/chat/conversations",
        body={"title": "smoke-chat-websocket"},
        timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
    )
    conversation_id = str(conversation.get("conversation_id", ""))
    checks.append(Check("Conversation create", bool(conversation_id), conversation_id))

    events: queue.Queue[dict[str, Any]] = queue.Queue()
    ws_ready = threading.Event()
    stop_ws = threading.Event()
    open_websocket_reader(
        url=websocket_url(client.base_url, conversation_id),
        cookies=cookie_header(client),
        output_queue=events,
        ready_event=ws_ready,
        stop_event=stop_ws,
        timeout=stream_timeout,
    )
    checks.append(Check("WebSocket connected", ws_ready.wait(timeout=10)))

    try:
        chat_start = api_request(
            client,
            "POST",
            "/chat",
            body={"message": message, "conversation_id": conversation_id},
            timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
        )
        request_id = str(chat_start.get("request_id", ""))
        checks.append(
            Check(
                "Chat start acknowledgement",
                chat_start.get("stream_started") is True and bool(request_id),
                str(chat_start),
            )
        )
        stream_result = wait_for_chat_terminal_events(
            events=events,
            request_id=request_id,
            timeout=stream_timeout,
        )
        terminal_error = _terminal_error_message(stream_result.error_event)
        checks.append(
            Check(
                "WS final received",
                stream_result.final_event is not None,
                terminal_error or stream_result.stream_exception,
            )
        )
        checks.append(
            Check(
                "WS done received",
                stream_result.done_event is not None,
                f"events_seen={len(stream_result.events)}",
            )
        )
        checks.append(
            Check(
                "No terminal WS error",
                stream_result.error_event is None
                and not stream_result.stream_exception,
                terminal_error or stream_result.stream_exception,
            )
        )
    finally:
        stop_ws.set()
        if conversation_id and not keep_conversation:
            try:
                api_request(
                    client,
                    "DELETE",
                    f"/chat/conversations/{conversation_id}",
                    timeout=DEFAULT_GET_TIMEOUT_SECONDS,
                )
            except Exception as error:
                checks.append(Check("Conversation cleanup", False, str(error)))
            else:
                checks.append(Check("Conversation cleanup", True, conversation_id))

    return checks


def _terminal_error_message(event: dict[str, Any] | None) -> str:
    """Return the terminal error message from one stream-error event."""
    if event is None:
        return ""
    data = event.get("data")
    if not isinstance(data, dict):
        return ""
    return str(data.get("message", ""))


def print_report(checks: list[Check]) -> None:
    """Print the compact green-check smoke report."""
    passed_count = sum(1 for check in checks if check.passed)
    failed_count = len(checks) - passed_count
    for check in checks:
        marker = OK_SYMBOL if check.passed else FAIL_SYMBOL
        suffix = f" - {check.detail}" if check.detail and not check.passed else ""
        print(f"{marker} {check.name}{suffix}", flush=True)
    print(f"Итог: passed={passed_count} failed={failed_count}", flush=True)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a live API smoke test for /chat plus /ws.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BIMEMO_BASE_URL", DEFAULT_BASE_URL),
        help="biMEMO base URL.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("BIMEMO_PASSWORD", ""),
        help="Password used for /auth/login or /auth/setup.",
    )
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument(
        "--stream-timeout",
        type=int,
        default=DEFAULT_STREAM_TIMEOUT_SECONDS,
    )
    parser.add_argument("--keep-conversation", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Run the smoke test and return a process exit code."""
    args = parse_args()
    if not args.password:
        raise SystemExit("--password or BIMEMO_PASSWORD is required")
    client = build_client(args.base_url, args.password)
    checks = run_smoke(
        client=client,
        message=args.message,
        stream_timeout=args.stream_timeout,
        keep_conversation=args.keep_conversation,
    )
    print_report(checks)
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
