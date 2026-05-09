#!/usr/bin/env python3
"""Smoke-test the live `/chat` start acknowledgement plus `/stream` final path."""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.memory_loop_api import APIClient, authenticate_client, request_json
from scripts.memory_loop_constants import (
    AUTH_EXEMPT_PATHS,
    CONTAINER_LOCAL_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_CHAT_TIMEOUT_SECONDS,
    DEFAULT_GET_TIMEOUT_SECONDS,
    RATE_LIMIT_BACKOFF_SECONDS,
    REQUEST_RETRY_LIMIT,
)


DEFAULT_STREAM_TIMEOUT_SECONDS = 90
DEFAULT_MESSAGE = (
    "Smoke test: answer with one short sentence. "
    "If no LLM is configured, return the normal missing-LLM fallback."
)
OK_SYMBOL = "✅"
FAIL_SYMBOL = "❌"


@dataclass(slots=True)
class Check:
    """One smoke-test checklist item."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class StreamResult:
    """Relevant terminal events observed for one chat request."""

    events: list[dict[str, Any]]
    final_event: dict[str, Any] | None = None
    error_event: dict[str, Any] | None = None
    done_event: dict[str, Any] | None = None
    stream_exception: str = ""


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
    """Build and authenticate one API client, with local-container fallback."""
    client = APIClient(base_url)
    try:
        authenticate(client, password)
        return client
    except Exception as error:
        error_text = str(error)
        if base_url != DEFAULT_BASE_URL:
            raise
        if "Connection refused" not in error_text and "Errno 111" not in error_text:
            raise
    fallback_client = APIClient(CONTAINER_LOCAL_BASE_URL)
    authenticate(fallback_client, password)
    print(
        f"Falling back to container-local API base URL {CONTAINER_LOCAL_BASE_URL}",
        flush=True,
    )
    return fallback_client


def open_stream_reader(
    *,
    client: APIClient,
    conversation_id: str,
    output_queue: queue.Queue[dict[str, Any]],
    ready_event: threading.Event,
    stop_event: threading.Event,
    timeout: int,
) -> threading.Thread:
    """Start a background reader for one SSE stream connection."""
    thread = threading.Thread(
        target=_read_stream_events,
        kwargs={
            "client": client,
            "conversation_id": conversation_id,
            "output_queue": output_queue,
            "ready_event": ready_event,
            "stop_event": stop_event,
            "timeout": timeout,
        },
        daemon=True,
    )
    thread.start()
    return thread


def _read_stream_events(
    *,
    client: APIClient,
    conversation_id: str,
    output_queue: queue.Queue[dict[str, Any]],
    ready_event: threading.Event,
    stop_event: threading.Event,
    timeout: int,
) -> None:
    """Read SSE `data:` frames and put decoded payloads on a queue."""
    params = urlencode({"conversation_id": conversation_id})
    request = Request(  # noqa: S310
        f"{client.base_url}/stream?{params}",
        method="GET",
    )
    try:
        with client._opener.open(request, timeout=timeout) as response:
            ready_event.set()
            while not stop_event.is_set():
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload_text = line.removeprefix("data:").strip()
                try:
                    output_queue.put(json.loads(payload_text))
                except json.JSONDecodeError as error:
                    output_queue.put(
                        {
                            "type": "stream_parse_error",
                            "detail": str(error),
                            "raw": payload_text,
                        }
                    )
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        ready_event.set()
        output_queue.put({"type": "stream_exception", "detail": str(error)})


def wait_for_chat_terminal_events(
    *,
    events: queue.Queue[dict[str, Any]],
    request_id: str,
    timeout: int,
) -> StreamResult:
    """Wait until the stream emits final/error and done for one request."""
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

    return StreamResult(
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
    """Run the live chat stream smoke test."""
    checks: list[Check] = []
    health = api_request(client, "GET", "/health")
    health_ok = health.get("status") in {"healthy", "ok"}
    checks.append(Check("API health", health_ok, str(health)))

    auth_status = api_request(client, "GET", "/auth/status")
    checks.append(
        Check(
            "Auth setup/login",
            bool(auth_status.get("authenticated")),
            str(auth_status),
        )
    )

    conversation = api_request(
        client,
        "POST",
        "/chat/conversations",
        body={"title": "smoke-chat-stream"},
        timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
    )
    conversation_id = str(conversation.get("conversation_id", ""))
    checks.append(Check("Conversation create", bool(conversation_id), conversation_id))

    events: queue.Queue[dict[str, Any]] = queue.Queue()
    stream_ready = threading.Event()
    stop_stream = threading.Event()
    open_stream_reader(
        client=client,
        conversation_id=conversation_id,
        output_queue=events,
        ready_event=stream_ready,
        stop_event=stop_stream,
        timeout=stream_timeout,
    )
    checks.append(
        Check(
            "Stream connected",
            stream_ready.wait(timeout=10),
            f"conversation_id={conversation_id}",
        )
    )

    chat_start: dict[str, Any] = {}
    stream_result = StreamResult(events=[])
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
                chat_start.get("stream_started") is True
                and bool(chat_start.get("conversation_id"))
                and bool(request_id),
                str(chat_start),
            )
        )
        checks.append(
            Check(
                "No duplicate HTTP final response",
                "structured_content" not in chat_start,
                "HTTP /chat returned start-only payload",
            )
        )
        if request_id:
            stream_result = wait_for_chat_terminal_events(
                events=events,
                request_id=request_id,
                timeout=stream_timeout,
            )
        final_answer = _final_answer(stream_result.final_event)
        terminal_error = _terminal_error_message(stream_result.error_event)
        checks.append(
            Check(
                "Stream final received",
                stream_result.final_event is not None,
                final_answer or terminal_error or stream_result.stream_exception,
            )
        )
        checks.append(
            Check(
                "Stream done received",
                stream_result.done_event is not None,
                f"events_seen={len(stream_result.events)}",
            )
        )
        checks.append(
            Check(
                "No terminal stream error",
                stream_result.error_event is None
                and not stream_result.stream_exception,
                terminal_error or stream_result.stream_exception,
            )
        )
    finally:
        stop_stream.set()
        if conversation_id and not keep_conversation:
            try:
                api_request(
                    client,
                    "DELETE",
                    f"/chat/conversations/{conversation_id}",
                    timeout=DEFAULT_GET_TIMEOUT_SECONDS,
                )
            except Exception as error:
                checks.append(
                    Check("Conversation cleanup", False, str(error)),
                )
            else:
                checks.append(Check("Conversation cleanup", True, conversation_id))

    return checks


def _final_answer(event: dict[str, Any] | None) -> str:
    """Return the visible answer from one stream-final SSE event."""
    if event is None:
        return ""
    data = event.get("data")
    if not isinstance(data, dict):
        return ""
    structured_content = data.get("structured_content")
    if not isinstance(structured_content, dict):
        return ""
    return str(structured_content.get("answer", ""))


def _terminal_error_message(event: dict[str, Any] | None) -> str:
    """Return the terminal error message from one stream-error SSE event."""
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
        description="Run a live API smoke test for /chat start ack plus /stream final.",
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
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Chat message to send.",
    )
    parser.add_argument(
        "--stream-timeout",
        type=int,
        default=DEFAULT_STREAM_TIMEOUT_SECONDS,
        help="Seconds to wait for stream terminal events.",
    )
    parser.add_argument(
        "--keep-conversation",
        action="store_true",
        help="Keep the smoke-test conversation instead of deleting it.",
    )
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
