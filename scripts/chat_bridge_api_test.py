#!/usr/bin/env python3
"""Run a real end-to-end chat-bridge check through the biMEMO HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


DEFAULT_BRIDGE_PROVIDER = "discord"
DEFAULT_SYNC_DIRECTION = "bidirectional"
DEFAULT_BRIDGE_MESSAGE_DIRECTION_INBOUND = "inbound"
DEFAULT_BRIDGE_MESSAGE_DIRECTION_OUTBOUND = "outbound"


@dataclass(frozen=True, slots=True)
class BridgeScenarioResult:
    """Outcome of one live chat-bridge check."""

    conversation_id: str
    bridge_link_id: str
    outbound_mapping_count: int
    inbound_mapping_count: int
    inbound_message_saved: bool
    passed: bool
    details: list[str]


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


def api_get(
    client: APIClient,
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_GET_TIMEOUT_SECONDS,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send a GET request through the shared stdlib API helper."""
    return request_json(
        client,
        "GET",
        path,
        body=None,
        params=params,
        timeout=timeout,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        extra_headers=extra_headers,
        authenticate_fn=authenticate,
    )


def api_post(
    client: APIClient,
    path: str,
    body: dict[str, Any] | None,
    timeout: int = DEFAULT_CHAT_TIMEOUT_SECONDS,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send a POST request through the shared stdlib API helper."""
    return request_json(
        client,
        "POST",
        path,
        body=body,
        params=None,
        timeout=timeout,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        extra_headers=extra_headers,
        authenticate_fn=authenticate,
    )


def get_reauth_token(client: APIClient, password: str) -> str:
    """Get a single-use reauth token for admin bridge operations."""
    payload = api_post(
        client,
        "/admin/reauth",
        {"password": password},
    )
    token = str(payload.get("reauth_token", "")).strip()
    if not token:
        raise RuntimeError("Failed to obtain /admin/reauth token")
    return token


def run_bridge_check(
    *,
    client: APIClient,
    password: str,
    bridge_inbound_token: str,
) -> BridgeScenarioResult:
    """Run one live bridge scenario through chat, admin, and inbound routes."""
    conversation_id = f"chat-bridge-api-{uuid.uuid4().hex[:12]}"
    details: list[str] = []

    initial_chat = api_post(
        client,
        "/chat",
        {
            "message": "Create a new web-led bridge test conversation.",
            "conversation_id": conversation_id,
        },
    )
    details.append(f"Initial chat response length: {len(initial_chat.get('text', ''))}")

    reauth_token = get_reauth_token(client, password)
    bridge_link = api_post(
        client,
        "/admin/chat-bridges",
        {
            "conversation_id": conversation_id,
            "provider": DEFAULT_BRIDGE_PROVIDER,
            "sync_direction": DEFAULT_SYNC_DIRECTION,
            "title": "Bridge API test room",
        },
        extra_headers={"X-Reauth-Token": reauth_token},
    )
    bridge_link_id = str(bridge_link["id"])
    details.append(f"Created bridge link {bridge_link_id}")

    stored_link = api_get(
        client,
        f"/admin/chat-bridges/conversations/{conversation_id}",
    )
    if stored_link.get("id") != bridge_link_id:
        details.append("Stored bridge lookup did not return the created link")

    api_post(
        client,
        "/chat",
        {
            "message": "Send one outbound bridge sync check after the link exists.",
            "conversation_id": conversation_id,
        },
    )

    outbound_links = api_get(
        client,
        f"/admin/chat-bridges/{bridge_link_id}/messages",
        params={"direction": DEFAULT_BRIDGE_MESSAGE_DIRECTION_OUTBOUND},
    )

    inbound_payload = api_post(
        client,
        "/bridge/providers/discord/messages",
        {
            "external_space_id": str(bridge_link["external_space_id"]),
            "external_channel_id": str(bridge_link["external_channel_id"]),
            "external_message_id": f"discord-message-{uuid.uuid4().hex[:8]}",
            "author_id": "discord-user-api-test",
            "author_name": "Bridge API Test",
            "content": "Inbound Discord bridge test message.",
        },
        extra_headers={"Authorization": f"Bearer {bridge_inbound_token}"},
    )
    inbound_message_id = str(inbound_payload.get("message_id", "")).strip()
    details.append(f"Inbound route saved message {inbound_message_id or 'missing'}")

    inbound_links = api_get(
        client,
        f"/admin/chat-bridges/{bridge_link_id}/messages",
        params={"direction": DEFAULT_BRIDGE_MESSAGE_DIRECTION_INBOUND},
    )
    conversation_messages = api_get(
        client,
        "/admin/memory/messages",
        params={"conversation_id": conversation_id, "limit": 20},
    )

    inbound_message_saved = any(
        str(message.get("id")) == inbound_message_id
        or str(message.get("content", "")).find("Inbound Discord bridge test message.")
        >= 0
        for message in conversation_messages.get("messages", [])
    )
    if not inbound_message_saved:
        details.append("Inbound message was not found in /admin/memory/messages")

    outbound_mapping_count = len(outbound_links)
    inbound_mapping_count = len(inbound_links)
    if outbound_mapping_count == 0:
        details.append("No outbound bridge mappings were recorded")
    if inbound_mapping_count == 0:
        details.append("No inbound bridge mappings were recorded")

    passed = (
        stored_link.get("id") == bridge_link_id
        and outbound_mapping_count > 0
        and inbound_mapping_count > 0
        and inbound_message_saved
    )
    return BridgeScenarioResult(
        conversation_id=conversation_id,
        bridge_link_id=bridge_link_id,
        outbound_mapping_count=outbound_mapping_count,
        inbound_mapping_count=inbound_mapping_count,
        inbound_message_saved=inbound_message_saved,
        passed=passed,
        details=details,
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a live chat-bridge API check against biMEMO.",
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
        "--bridge-inbound-token",
        default=os.environ.get("DISCORD_BRIDGE_TOKEN", ""),
        help="Bearer token for /bridge/providers/discord/messages.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path for a JSON report.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the live bridge check and print a compact report."""
    args = parse_args()
    if not args.password:
        raise SystemExit("--password or BIMEMO_PASSWORD is required")
    if not args.bridge_inbound_token:
        raise SystemExit("--bridge-inbound-token or DISCORD_BRIDGE_TOKEN is required")
    client = APIClient(
        base_url=args.base_url or CONTAINER_LOCAL_BASE_URL,
        auth_password=args.password,
    )
    authenticate(client, args.password)
    result = run_bridge_check(
        client=client,
        password=args.password,
        bridge_inbound_token=args.bridge_inbound_token,
    )

    print(
        "chat bridge api test: "
        f"conversation={result.conversation_id} "
        f"bridge={result.bridge_link_id} "
        f"outbound={result.outbound_mapping_count} "
        f"inbound={result.inbound_mapping_count} "
        f"inbound_saved={result.inbound_message_saved} "
        f"passed={result.passed}"
    )
    for detail in result.details:
        print(f"- {detail}")

    if args.output_json:
        report = {
            "conversation_id": result.conversation_id,
            "bridge_link_id": result.bridge_link_id,
            "outbound_mapping_count": result.outbound_mapping_count,
            "inbound_mapping_count": result.inbound_mapping_count,
            "inbound_message_saved": result.inbound_message_saved,
            "passed": result.passed,
            "details": result.details,
        }
        Path(args.output_json).write_text(
            json.dumps(report, indent=2, ensure_ascii=True)
        )

    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
