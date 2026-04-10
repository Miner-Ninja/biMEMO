#!/usr/bin/env python3
"""Memory health diagnostic test runner.

Captures memory subsystem health snapshots before and after a
chat test run, then prints the delta to show how memory metrics
changed during the test.

Usage inside the container:
    python scripts/memory_health_test.py --password <pass>
    python scripts/memory_health_test.py --password <pass> --messages 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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


TEST_MESSAGES = [
    "Привет! Как дела?",
    "Что ты умеешь делать?",
    "Расскажи о своих инструментах",  # noqa: RUF001
    "Какие файлы ты можешь читать?",
    "А что насчёт поиска в интернете?",  # noqa: RUF001
    "Помнишь что мы обсуждали раньше?",
    "Расскажи подробнее",
    "Спасибо, это полезно",
]


def _get_health(client: APIClient) -> dict:
    """Fetch memory health snapshot."""
    return request_json(
        client,
        "GET",
        "/admin/memory/health",
        body=None,
        params=None,
        timeout=DEFAULT_GET_TIMEOUT_SECONDS,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
    )


def _print_snapshot(label: str, snapshot: dict) -> None:
    """Print one health snapshot."""
    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))


def _print_delta(
    baseline: dict,
    after: dict,
) -> None:
    """Print the delta between two snapshots."""
    print(f"\n{'=' * 50}")
    print("  DELTA (after - baseline)")
    print(f"{'=' * 50}")

    bp = baseline.get("pipeline", {})
    ap = after.get("pipeline", {})
    for key in ("messages", "records", "summaries"):
        diff = ap.get(key, 0) - bp.get(key, 0)
        print(f"  pipeline.{key}: {bp.get(key, 0)} → {ap.get(key, 0)} (Δ{diff:+d})")

    br = baseline.get("retrieval", {})
    ar = after.get("retrieval", {})
    recall_diff = ar.get("total_recalls", 0) - br.get("total_recalls", 0)
    print(f"  retrieval.total_recalls: Δ{recall_diff:+d}")
    print(f"  retrieval.avg_results: {ar.get('avg_results_per_recall', 0)}")


def main() -> None:
    """Run the memory health diagnostic test."""
    parser = argparse.ArgumentParser(
        description="Memory health diagnostic test",
    )
    parser.add_argument("--password", required=True)
    parser.add_argument("--messages", type=int, default=len(TEST_MESSAGES))
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()

    base_url = args.base_url or CONTAINER_LOCAL_BASE_URL or DEFAULT_BASE_URL
    client = APIClient(base_url, auth_password=args.password)
    authenticate_client(
        client,
        args.password,
        get_timeout=DEFAULT_GET_TIMEOUT_SECONDS,
        chat_timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
    )
    print(f"Authenticated to {base_url}")

    baseline = _get_health(client)
    _print_snapshot("BASELINE", baseline)

    messages = TEST_MESSAGES[: args.messages]
    print(f"\nSending {len(messages)} messages...")

    for i, msg in enumerate(messages, 1):
        preview = msg[:50]
        print(f"  [{i}/{len(messages)}] {preview}")
        try:
            resp = request_json(
                client,
                "POST",
                "/chat",
                body={"message": msg},
                params=None,
                timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
                auth_exempt_paths=AUTH_EXEMPT_PATHS,
                request_retry_limit=REQUEST_RETRY_LIMIT,
                rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
            )
            text = resp.get("text", "")[:60]
            print(f"           → {text}...")
        except Exception as e:
            print(f"           ✗ {e}")

    after = _get_health(client)
    _print_snapshot("AFTER", after)
    _print_delta(baseline, after)
    print("\nDone.")


if __name__ == "__main__":
    main()
