#!/usr/bin/env python3
"""Run a real end-to-end file-context check through the biMEMO HTTP API.

This script authenticates against a live biMEMO instance, uploads a small
set of files, exercises the file-context pipeline through chat and admin
routes, and prints a compact report that shows which file mode was stored and
which mode was used during the last chat request.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
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


INLINE_STORED_MODE = "inline"
PREVIEW_STORED_MODE = "preview"
SUMMARIZED_STORED_MODE = "summarized"
CHUNKED_CHAT_MODE = "chunked_recall"

INLINE_BLOCK_MARKER = "--- File:"
PREVIEW_BLOCK_MARKER = "Only a preview or selected chunks are shown below."
SUMMARY_BLOCK_MARKER = "File summary:"
CHUNK_BLOCK_MARKER = "Relevant file chunks:"

MULTIPART_HEADER_TEMPLATE = "multipart/form-data; boundary={boundary}"
CRLF = "\r\n"


@dataclass(frozen=True, slots=True)
class FileScenario:
    """One live file-context scenario."""

    name: str
    file_name: str
    mime_type: str
    content: bytes
    chat_message: str
    expected_stored_mode: str
    expected_last_chat_mode: str
    expected_prompt_marker: str


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Outcome for one live file-context scenario."""

    name: str
    file_id: str
    stored_mode: str
    last_chat_mode: str
    prompt_block_count: int
    expected_prompt_marker: str
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
        authenticate_fn=authenticate,
    )


def api_post(
    client: APIClient,
    path: str,
    body: dict[str, Any] | None,
    timeout: int = DEFAULT_CHAT_TIMEOUT_SECONDS,
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
        authenticate_fn=authenticate,
    )


def api_delete(
    client: APIClient,
    path: str,
    timeout: int = DEFAULT_GET_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send a DELETE request through the shared stdlib API helper."""
    return request_json(
        client,
        "DELETE",
        path,
        body=None,
        params=None,
        timeout=timeout,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        authenticate_fn=authenticate,
    )


def upload_file(
    client: APIClient,
    *,
    file_name: str,
    mime_type: str,
    content: bytes,
    timeout: int = DEFAULT_CHAT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Upload one file through the real multipart API."""
    boundary = f"bimemo-boundary-{uuid.uuid4().hex}"
    body = build_multipart_body(
        boundary=boundary,
        field_name="file",
        file_name=file_name,
        mime_type=mime_type,
        content=content,
    )
    request = Request(  # noqa: S310
        f"{client.base_url}/files/upload",
        data=body,
        method="POST",
    )
    request.add_header(
        "Content-Type",
        MULTIPART_HEADER_TEMPLATE.format(boundary=boundary),
    )
    if client.csrf_token:
        request.add_header("X-CSRF-Token", client.csrf_token)
    try:
        with client._opener.open(request, timeout=timeout) as response:
            raw_body = response.read().decode()
    except HTTPError as error:
        raw_body = error.read().decode()
        raise RuntimeError(f"POST /files/upload -> {error.code}: {raw_body}") from error
    return json.loads(raw_body) if raw_body.strip() else {}


def build_multipart_body(
    *,
    boundary: str,
    field_name: str,
    file_name: str,
    mime_type: str,
    content: bytes,
) -> bytes:
    """Build one multipart/form-data request body for a single file."""
    return b"".join(
        [
            f"--{boundary}{CRLF}".encode(),
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{file_name}"{CRLF}'
            ).encode(),
            f"Content-Type: {mime_type}{CRLF}{CRLF}".encode(),
            content,
            f"{CRLF}--{boundary}--{CRLF}".encode(),
        ]
    )


def build_scenarios() -> list[FileScenario]:
    """Return the built-in live file-context scenarios."""
    inline_content = b"Short deployment note."
    preview_lines = [
        f"Preview test line {index}: keep this medium file in preview mode."
        for index in range(1, 141)
    ]
    large_lines = [
        "service_name=bimemo",
        "healthcheck_path=/health",
        "rollout_strategy=blue-green",
        "retry_limit=5",
        "status_page_enabled=true",
    ] * 200
    return [
        FileScenario(
            name="inline-small-file",
            file_name="inline-note.txt",
            mime_type="text/plain",
            content=inline_content,
            chat_message="Please use this file in your answer.",
            expected_stored_mode=INLINE_STORED_MODE,
            expected_last_chat_mode=INLINE_STORED_MODE,
            expected_prompt_marker=INLINE_BLOCK_MARKER,
        ),
        FileScenario(
            name="preview-medium-file",
            file_name="preview-report.txt",
            mime_type="text/plain",
            content="\n".join(preview_lines).encode("utf-8"),
            chat_message="Give me a short overview of this file.",
            expected_stored_mode=PREVIEW_STORED_MODE,
            expected_last_chat_mode=PREVIEW_STORED_MODE,
            expected_prompt_marker=PREVIEW_BLOCK_MARKER,
        ),
        FileScenario(
            name="chunked-large-file",
            file_name="deploy.conf",
            mime_type="text/plain",
            content="\n".join(large_lines).encode("utf-8"),
            chat_message="Show the exact /health line from this file.",
            expected_stored_mode=SUMMARIZED_STORED_MODE,
            expected_last_chat_mode=CHUNKED_CHAT_MODE,
            expected_prompt_marker=CHUNK_BLOCK_MARKER,
        ),
        FileScenario(
            name="summarized-large-file",
            file_name="runbook.md",
            mime_type="text/markdown",
            content=(
                "\n".join(
                    [
                        "# Runbook",
                        "Prepare rollback plan.",
                        "Restart edge workers after deploy.",
                        "Document health checks in the incident note.",
                    ]
                    * 250
                )
            ).encode("utf-8"),
            chat_message="Summarize this file.",
            expected_stored_mode=SUMMARIZED_STORED_MODE,
            expected_last_chat_mode=SUMMARIZED_STORED_MODE,
            expected_prompt_marker=SUMMARY_BLOCK_MARKER,
        ),
    ]


def run_scenario(
    client: APIClient,
    scenario: FileScenario,
    *,
    keep_files: bool,
) -> ScenarioResult:
    """Run one full upload -> chat -> admin context verification flow."""
    upload_payload = upload_file(
        client,
        file_name=scenario.file_name,
        mime_type=scenario.mime_type,
        content=scenario.content,
    )
    file_id = str(upload_payload["id"])
    details: list[str] = [f"uploaded file id: {file_id}"]
    try:
        files_payload = api_get(client, "/admin/memory/files")
        if not any(
            item.get("id") == file_id for item in files_payload.get("files", [])
        ):
            return ScenarioResult(
                name=scenario.name,
                file_id=file_id,
                stored_mode="",
                last_chat_mode="",
                prompt_block_count=0,
                expected_prompt_marker=scenario.expected_prompt_marker,
                passed=False,
                details=[
                    *details,
                    "uploaded file was not visible in /admin/memory/files",
                ],
            )

        stored_context_payload = api_get(
            client,
            f"/admin/memory/files/{file_id}/context",
        ).get("file_context")
        if not isinstance(stored_context_payload, dict):
            return ScenarioResult(
                name=scenario.name,
                file_id=file_id,
                stored_mode="",
                last_chat_mode="",
                prompt_block_count=0,
                expected_prompt_marker=scenario.expected_prompt_marker,
                passed=False,
                details=[*details, "stored file context was missing after upload"],
            )

        stored_mode = str(stored_context_payload.get("processing_mode", ""))
        details.append(f"stored mode: {stored_mode}")
        api_post(
            client,
            "/chat",
            {
                "message": scenario.chat_message,
                "file_ids": [file_id],
            },
        )
        final_context_payload = api_get(
            client,
            f"/admin/memory/files/{file_id}/context",
        ).get("file_context")
        if not isinstance(final_context_payload, dict):
            return ScenarioResult(
                name=scenario.name,
                file_id=file_id,
                stored_mode=stored_mode,
                last_chat_mode="",
                prompt_block_count=0,
                expected_prompt_marker=scenario.expected_prompt_marker,
                passed=False,
                details=[*details, "final file context was missing after chat"],
            )

        last_usage = final_context_payload.get("last_usage") or {}
        last_chat_mode = str(last_usage.get("processing_mode", ""))
        prompt_blocks = [
            str(block) for block in final_context_payload.get("prompt_blocks", [])
        ]
        details.append(f"last chat mode: {last_chat_mode}")
        details.append(f"prompt blocks: {len(prompt_blocks)}")

        failures: list[str] = []
        if stored_mode != scenario.expected_stored_mode:
            failures.append(
                "expected stored mode "
                f"{scenario.expected_stored_mode}, got {stored_mode}"
            )
        if last_chat_mode != scenario.expected_last_chat_mode:
            failures.append(
                "expected last chat mode "
                f"{scenario.expected_last_chat_mode}, got {last_chat_mode}"
            )
        if not any(scenario.expected_prompt_marker in block for block in prompt_blocks):
            failures.append(
                "expected prompt marker was not present in the admin prompt preview"
            )
        if not prompt_blocks:
            failures.append("prompt preview was empty")

        return ScenarioResult(
            name=scenario.name,
            file_id=file_id,
            stored_mode=stored_mode,
            last_chat_mode=last_chat_mode,
            prompt_block_count=len(prompt_blocks),
            expected_prompt_marker=scenario.expected_prompt_marker,
            passed=not failures,
            details=details + failures,
        )
    finally:
        if not keep_files:
            try:
                api_delete(client, f"/files/{file_id}")
            except Exception as error:
                print(
                    f"Cleanup warning for {scenario.file_name}: {error}",
                    file=sys.stderr,
                )


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Run a live end-to-end file-context test through the biMEMO API."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"biMEMO base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Password used for /auth/login or /auth/setup.",
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep uploaded files instead of deleting them during cleanup.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path where the JSON report should be written.",
    )
    return parser.parse_args()


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


def print_report(results: list[ScenarioResult]) -> None:
    """Print a human-readable report for all scenarios."""
    passed_count = sum(1 for result in results if result.passed)
    print(
        f"File-context API test: {passed_count}/{len(results)} scenarios passed",
        flush=True,
    )
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(
            (
                f"[{status}] {result.name}: stored={result.stored_mode}, "
                "last_chat="
                f"{result.last_chat_mode}, prompt_blocks={result.prompt_block_count}"
            ),
            flush=True,
        )
        for detail in result.details:
            print(f"  - {detail}", flush=True)


def serialize_results(results: list[ScenarioResult]) -> dict[str, Any]:
    """Convert scenario results into a JSON-safe report."""
    return {
        "scenario_count": len(results),
        "passed_count": sum(1 for result in results if result.passed),
        "results": [
            {
                "name": result.name,
                "file_id": result.file_id,
                "stored_mode": result.stored_mode,
                "last_chat_mode": result.last_chat_mode,
                "prompt_block_count": result.prompt_block_count,
                "expected_prompt_marker": result.expected_prompt_marker,
                "passed": result.passed,
                "details": result.details,
            }
            for result in results
        ],
    }


def main() -> int:
    """Run the live file-context API test harness."""
    args = parse_args()
    client = build_client(args.base_url, args.password)
    results = [
        run_scenario(
            client,
            scenario,
            keep_files=args.keep_files,
        )
        for scenario in build_scenarios()
    ]
    print_report(results)
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.write_text(
            json.dumps(serialize_results(results), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
