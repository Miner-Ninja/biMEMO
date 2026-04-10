#!/usr/bin/env python3
"""Run a memory evaluation loop through the biMEMO HTTP API.

This script conducts a scripted conversation, waits for background memory
processing, inspects the resulting memory graph through admin routes, and
produces a report with concrete improvement recommendations.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.memory_loop_api import (
    APIClient,
    authenticate_client,
    authenticate_with_runtime_fallback_impl,
    is_rate_limit_error,
    request_json,
)
from scripts.memory_loop_constants import (
    AUTH_EXEMPT_PATHS,
    CHAT_RETRY_LIMIT,
    CONTAINER_LOCAL_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_CHAT_DELAY_SECONDS,
    DEFAULT_CHAT_TIMEOUT_SECONDS,
    DEFAULT_GET_TIMEOUT_SECONDS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PIPELINE_TIMEOUT_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_SCENARIO_PROFILE,
    DISTILLATION_TRIGGER_THRESHOLD,
    EXPECTED_DIALOG_STEPS_PER_OBSERVATION,
    EXPECTED_MESSAGE_MULTIPLIER,
    FULL_SCENARIO_MESSAGE_COVERAGE,
    MEMORY_LOOP_CONVERSATION_PREFIX,
    MIN_SCENARIO_MESSAGE_COVERAGE,
    PAGE_SIZE,
    PHASE_NAMES,
    RATE_LIMIT_BACKOFF_SECONDS,
    REQUEST_RETRY_LIMIT,
    SCENARIO_DISTILLATION_BUFFER,
)
from scripts.memory_loop_evaluation import (
    build_findings,
    build_recommendations,
    evaluate_scenario,
)
from scripts.memory_loop_matchers import (
    normalize_string_list,
    response_confirms_current_long_term_style,
    response_has_old_style_as_active_long_term,
    response_only_mentions_old_style,
    response_prefers_current_style,
)
from scripts.memory_loop_models import (
    ChatDialogStepResult,
    EvaluationFinding,
    PhaseSummary,
    ScenarioReport,
    ScenarioState,
)
from scripts.memory_loop_output import (
    print_report,
    print_series_summary,
    serialize_report,
    summarize_reports,
)
from scripts.memory_loop_phase_summary import build_phase_breakdown
from scripts.memory_loop_retrieval import build_retrieval_report
from scripts.memory_loop_runtime import (
    cleanup_old_memory_loop_conversations_impl,
    collect_memory_loop_conversation_ids,
    fetch_scenario_state_impl,
    send_scenario_dialog_steps_impl,
    wait_for_scenario_memory_impl,
)
from scripts.memory_loop_runtime import (
    collect_paginated_entries as collect_paginated_entries_impl,
)
from scripts.memory_loop_scenarios import (
    SCENARIO_TEMPLATE_PROFILES,
)
from scripts.memory_loop_summary_hygiene import (
    build_long_term_contamination_report,
)


__all__ = [
    "CHAT_RETRY_LIMIT",
    "DEFAULT_BASE_URL",
    "DEFAULT_CHAT_DELAY_SECONDS",
    "DEFAULT_CHAT_TIMEOUT_SECONDS",
    "DEFAULT_GET_TIMEOUT_SECONDS",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_PIPELINE_TIMEOUT_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_SCENARIO_PROFILE",
    "DISTILLATION_TRIGGER_THRESHOLD",
    "EXPECTED_DIALOG_STEPS_PER_OBSERVATION",
    "EXPECTED_MESSAGE_MULTIPLIER",
    "FULL_SCENARIO_MESSAGE_COVERAGE",
    "MIN_SCENARIO_MESSAGE_COVERAGE",
    "PAGE_SIZE",
    "PHASE_NAMES",
    "RATE_LIMIT_BACKOFF_SECONDS",
    "SCENARIO_DISTILLATION_BUFFER",
    "SCENARIO_TEMPLATE_PROFILES",
    "APIClient",
    "ChatDialogStepResult",
    "EvaluationFinding",
    "PhaseSummary",
    "ScenarioReport",
    "ScenarioState",
    "authenticate",
    "authenticate_with_runtime_fallback",
    "build_findings",
    "build_long_term_contamination_report",
    "build_phase_breakdown",
    "build_recommendations",
    "build_retrieval_report",
    "build_scenario_prompts",
    "cleanup_old_memory_loop_conversations",
    "collect_paginated_entries",
    "evaluate_scenario",
    "fetch_scenario_state",
    "is_rate_limit_error",
    "normalize_string_list",
    "parse_args",
    "print_report",
    "print_series_summary",
    "response_confirms_current_long_term_style",
    "response_has_old_style_as_active_long_term",
    "response_only_mentions_old_style",
    "response_prefers_current_style",
    "run_iteration",
    "send_scenario_dialog_steps",
    "serialize_report",
    "summarize_reports",
    "wait_for_scenario_memory",
]


def _api_client_get(
    self: APIClient,
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_GET_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send a GET request and return JSON data."""
    return request_json(
        self,
        "GET",
        path,
        body=None,
        params=params,
        timeout=timeout,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        authenticate_fn=authenticate,
        sleep_fn=time.sleep,
    )


def _api_client_post(
    self: APIClient,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = DEFAULT_CHAT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send a POST request and return JSON data."""
    return request_json(
        self,
        "POST",
        path,
        body=body,
        params=None,
        timeout=timeout,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        authenticate_fn=authenticate,
        sleep_fn=time.sleep,
    )


def _api_client_delete(
    self: APIClient,
    path: str,
    timeout: int = DEFAULT_GET_TIMEOUT_SECONDS,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send a DELETE request and return JSON data."""
    return request_json(
        self,
        "DELETE",
        path,
        body=None,
        params=None,
        timeout=timeout,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        extra_headers=extra_headers,
        authenticate_fn=authenticate,
        sleep_fn=time.sleep,
    )


APIClient.get = _api_client_get
APIClient.post = _api_client_post
APIClient.delete = _api_client_delete


def authenticate(client: APIClient, password: str) -> None:
    """Authenticate against biMEMO and cache the CSRF token."""
    authenticate_client(
        client,
        password,
        get_timeout=DEFAULT_GET_TIMEOUT_SECONDS,
        chat_timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
        auth_exempt_paths=AUTH_EXEMPT_PATHS,
        request_retry_limit=REQUEST_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
    )


def authenticate_with_runtime_fallback(
    base_url: str,
    password: str,
) -> APIClient:
    """Authenticate and fall back to the container-local API when needed."""
    return authenticate_with_runtime_fallback_impl(
        base_url,
        password,
        client_factory=APIClient,
        authenticate_fn=authenticate,
        default_base_url=DEFAULT_BASE_URL,
        container_local_base_url=CONTAINER_LOCAL_BASE_URL,
    )


def build_scenario_prompts(
    scenario_tag: str,
    scenario_profile: str = DEFAULT_SCENARIO_PROFILE,
) -> list[str]:
    """Render one named evaluation scenario with a unique tag."""
    scenario_templates = SCENARIO_TEMPLATE_PROFILES[scenario_profile]
    minimum_prompt_count = DISTILLATION_TRIGGER_THRESHOLD + SCENARIO_DISTILLATION_BUFFER
    if len(scenario_templates) < minimum_prompt_count:
        raise RuntimeError(
            "Scenario templates must exceed the distiller threshold "
            "to trigger a fresh summary on a clean database."
        )
    return [
        template.format(scenario_tag=scenario_tag) for template in scenario_templates
    ]


def _collect_memory_loop_conversation_ids(messages: list[dict[str, Any]]) -> list[str]:
    """Extract unique prior memory-loop conversation IDs from messages."""
    return collect_memory_loop_conversation_ids(
        messages,
        conversation_prefix=MEMORY_LOOP_CONVERSATION_PREFIX,
    )


def collect_paginated_entries(
    client: APIClient,
    path: str,
    key: str,
    *,
    extra_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect all paginated entries from a list-style admin endpoint."""
    return collect_paginated_entries_impl(
        client,
        path,
        key,
        page_size=PAGE_SIZE,
        get_timeout=DEFAULT_GET_TIMEOUT_SECONDS,
        extra_params=extra_params,
    )


def cleanup_old_memory_loop_conversations(client: APIClient, password: str) -> int:
    """Delete prior memory-loop conversations so old test memory stays isolated."""
    return cleanup_old_memory_loop_conversations_impl(
        client,
        password,
        collect_entries_fn=collect_paginated_entries,
        get_timeout=DEFAULT_GET_TIMEOUT_SECONDS,
        chat_timeout=DEFAULT_CHAT_TIMEOUT_SECONDS,
        conversation_prefix=MEMORY_LOOP_CONVERSATION_PREFIX,
    )


def fetch_scenario_state(client: APIClient, conversation_id: str) -> ScenarioState:
    """Fetch all messages, records, summaries, and quality data."""
    return fetch_scenario_state_impl(
        client,
        conversation_id,
        collect_entries_fn=collect_paginated_entries,
    )


def wait_for_scenario_memory(
    client: APIClient,
    conversation_id: str,
    expected_dialog_step_count: int,
    timeout_seconds: int,
    poll_interval_seconds: int,
    required_record_count: int = 1,
    required_message_coverage: float = MIN_SCENARIO_MESSAGE_COVERAGE,
) -> ScenarioState:
    """Wait until scenario messages are stored and memory counts stabilize."""
    expected_message_count = expected_dialog_step_count * EXPECTED_MESSAGE_MULTIPLIER
    return wait_for_scenario_memory_impl(
        client,
        conversation_id,
        expected_message_count=expected_message_count,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        summary_trigger_threshold=DISTILLATION_TRIGGER_THRESHOLD,
        required_record_count=required_record_count,
        required_message_coverage=required_message_coverage,
        full_scenario_message_coverage=FULL_SCENARIO_MESSAGE_COVERAGE,
        fetch_state_fn=fetch_scenario_state,
        monotonic_fn=time.monotonic,
        sleep_fn=time.sleep,
    )


def send_scenario_dialog_steps(
    client: APIClient,
    conversation_id: str,
    prompts: list[str],
    delay_seconds: float,
) -> list[ChatDialogStepResult]:
    """Send the scripted conversation through the chat API."""
    return send_scenario_dialog_steps_impl(
        client,
        conversation_id,
        prompts,
        chat_retry_limit=CHAT_RETRY_LIMIT,
        rate_limit_backoff_seconds=RATE_LIMIT_BACKOFF_SECONDS,
        delay_seconds=delay_seconds,
        is_rate_limit_error_fn=is_rate_limit_error,
        monotonic_fn=time.monotonic,
        sleep_fn=time.sleep,
    )


def run_iteration(
    client: APIClient,
    iteration_index: int,
    *,
    scenario_profile: str,
    delay_seconds: float,
    pipeline_timeout_seconds: int,
    poll_interval_seconds: int,
) -> ScenarioReport:
    """Run one full scenario iteration and return the evaluation report."""
    scenario_tag = f"memory-loop-{iteration_index}-{uuid.uuid4().hex[:8]}"
    conversation_id = f"{scenario_tag}-conversation"
    prompts = build_scenario_prompts(scenario_tag, scenario_profile)

    print("\n" + "=" * 72, flush=True)
    print(
        "Starting scenario iteration "
        f"{iteration_index}: {scenario_tag} "
        f"[profile={scenario_profile}]",
        flush=True,
    )
    print("=" * 72, flush=True)

    dialog_step_results = send_scenario_dialog_steps(
        client,
        conversation_id,
        prompts,
        delay_seconds,
    )
    required_record_count = min(
        DISTILLATION_TRIGGER_THRESHOLD,
        max(1, math.ceil(len(prompts) / EXPECTED_DIALOG_STEPS_PER_OBSERVATION)),
    )
    print("\nWaiting for memory pipeline to settle...", flush=True)
    state = wait_for_scenario_memory(
        client,
        conversation_id,
        expected_dialog_step_count=len(prompts),
        timeout_seconds=pipeline_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        required_record_count=required_record_count,
        required_message_coverage=MIN_SCENARIO_MESSAGE_COVERAGE,
    )
    return evaluate_scenario(
        scenario_tag=scenario_tag,
        conversation_id=conversation_id,
        state=state,
        dialog_step_results=dialog_step_results,
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the memory loop test."""
    parser = argparse.ArgumentParser(
        description="Run a scripted biMEMO memory evaluation loop.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="biMEMO API base URL.",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Password used for /auth/login or /auth/setup.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=(
            "Maximum number of scenario iterations to run. "
            "Default: 1. Increase this only when you intentionally want a series."
        ),
    )
    parser.add_argument(
        "--scenario-profile",
        choices=sorted(SCENARIO_TEMPLATE_PROFILES),
        default=DEFAULT_SCENARIO_PROFILE,
        help="Named scripted scenario profile to run.",
    )
    parser.add_argument(
        "--chat-delay",
        type=float,
        default=DEFAULT_CHAT_DELAY_SECONDS,
        help="Delay between scripted dialog steps in seconds.",
    )
    parser.add_argument(
        "--pipeline-timeout",
        type=int,
        default=DEFAULT_PIPELINE_TIMEOUT_SECONDS,
        help="How long to wait for dialog recorder/distiller stabilization.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Polling interval while waiting for memory stabilization.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to save the final report JSON.",
    )
    parser.add_argument(
        "--stop-on-pass",
        action="store_true",
        help="Stop the loop as soon as one iteration passes.",
    )
    parser.add_argument(
        "--keep-existing-memory-loop-conversations",
        action="store_true",
        help=(
            "Keep older memory-loop conversations instead of deleting them "
            "before the run."
        ),
    )
    return parser.parse_args()


def main() -> int:
    """Authenticate, run the loop, and print actionable recommendations."""
    arguments = parse_args()
    client = authenticate_with_runtime_fallback(
        arguments.base_url,
        arguments.password,
    )
    if arguments.keep_existing_memory_loop_conversations:
        print(
            "Keeping older memory-loop conversations before the new run.",
            flush=True,
        )
    else:
        deleted_conversation_count = cleanup_old_memory_loop_conversations(
            client,
            arguments.password,
        )
        if deleted_conversation_count > 0:
            print(
                "Deleted old memory-loop conversations before the new run: "
                f"{deleted_conversation_count}",
                flush=True,
            )
    if arguments.max_iterations > 1:
        print(
            "Running an explicit multi-iteration series: "
            f"{arguments.max_iterations} iterations",
            flush=True,
        )

    reports: list[ScenarioReport] = []
    for iteration_index in range(1, arguments.max_iterations + 1):
        report = run_iteration(
            client,
            iteration_index,
            scenario_profile=arguments.scenario_profile,
            delay_seconds=arguments.chat_delay,
            pipeline_timeout_seconds=arguments.pipeline_timeout,
            poll_interval_seconds=arguments.poll_interval,
        )
        reports.append(report)
        print_report(report)
        if report.passed and arguments.stop_on_pass:
            break

    final_report = serialize_report(reports[-1])
    final_report["all_iterations"] = [serialize_report(report) for report in reports]
    final_report["series_summary"] = summarize_reports(reports)
    print_series_summary(final_report["series_summary"])

    if arguments.output_json:
        output_path = Path(arguments.output_json)
        output_path.write_text(
            json.dumps(final_report, ensure_ascii=False, indent=2) + "\n"
        )
        print(f"Saved report to {output_path}", flush=True)

    return 0 if reports[-1].passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
