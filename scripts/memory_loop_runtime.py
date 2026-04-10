"""Runtime helpers for the memory loop test harness."""

from __future__ import annotations

import time
from typing import Any

from scripts.memory_loop_matchers import (
    extract_reflected_record_ids,
    normalize_string_list,
)
from scripts.memory_loop_models import (
    ChatDialogStepResult,
    ScenarioState,
)
from scripts.memory_loop_summary_hygiene import (
    build_long_term_contamination_report,
)


def collect_memory_loop_conversation_ids(
    messages: list[dict[str, Any]],
    *,
    conversation_prefix: str,
) -> list[str]:
    """Extract unique prior memory-loop conversation IDs from messages."""
    conversation_ids: list[str] = []
    for message in messages:
        conversation_id = str(message.get("conversation_id", "")).strip()
        if (
            conversation_id.startswith(conversation_prefix)
            and conversation_id not in conversation_ids
        ):
            conversation_ids.append(conversation_id)
    return conversation_ids


def collect_paginated_entries(
    client: Any,
    path: str,
    key: str,
    *,
    page_size: int,
    get_timeout: int,
    extra_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect all paginated entries from a list-style admin endpoint."""
    entries: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {"limit": page_size, "offset": offset}
        if extra_params:
            params.update(extra_params)
        payload = client.get(path, params=params, timeout=get_timeout)
        batch = payload.get(key, [])
        if not isinstance(batch, list):
            raise RuntimeError(f"{path} returned a non-list payload for {key}")
        entries.extend(batch)
        if len(batch) < page_size:
            return entries
        offset += page_size


def cleanup_old_memory_loop_conversations_impl(
    client: Any,
    password: str,
    *,
    collect_entries_fn: Any,
    get_timeout: int,
    chat_timeout: int,
    conversation_prefix: str,
) -> int:
    """Delete prior memory-loop conversations so old test memory stays isolated."""
    messages = collect_entries_fn(
        client,
        "/admin/memory/messages",
        "messages",
    )
    conversation_ids = collect_memory_loop_conversation_ids(
        messages,
        conversation_prefix=conversation_prefix,
    )
    if not conversation_ids:
        return 0

    reauth_payload = client.post(
        "/admin/reauth",
        {"password": password},
        timeout=chat_timeout,
    )
    reauth_token = str(reauth_payload.get("reauth_token", "")).strip()
    if not reauth_token:
        raise RuntimeError("Failed to obtain re-authentication token")

    deleted_count = 0
    for conversation_id in conversation_ids:
        client.delete(
            f"/admin/conversations/{conversation_id}",
            timeout=get_timeout,
            extra_headers={"X-Reauth-Token": reauth_token},
        )
        deleted_count += 1
        if deleted_count < len(conversation_ids):
            reauth_payload = client.post(
                "/admin/reauth",
                {"password": password},
                timeout=chat_timeout,
            )
            reauth_token = str(reauth_payload.get("reauth_token", "")).strip()
            if not reauth_token:
                raise RuntimeError("Failed to refresh re-authentication token")
    return deleted_count


def fetch_scenario_state_impl(
    client: Any,
    conversation_id: str,
    *,
    collect_entries_fn: Any,
) -> ScenarioState:
    """Fetch all messages, records, summaries, and quality data."""
    messages = collect_entries_fn(
        client,
        "/admin/memory/messages",
        "messages",
        extra_params={"conversation_id": conversation_id},
    )
    records = collect_entries_fn(
        client,
        "/admin/memory/records",
        "records",
    )
    summaries = collect_entries_fn(
        client,
        "/admin/memory/summaries",
        "summaries",
    )
    quality_report = client.get("/admin/memory/quality")
    dialog_recorder_trace_entries = client.get(
        "/admin/memory/dialog-recorder-trace",
        params={"limit": 500},
    ).get("entries", [])
    distiller_trace_entries = client.get(
        "/admin/memory/distiller-trace",
        params={"limit": 500},
    ).get("entries", [])

    scenario_message_ids = {str(message["id"]) for message in messages}
    scenario_observations = [
        record
        for record in records
        if scenario_message_ids.intersection(
            normalize_string_list(record.get("source_message_ids", []))
        )
    ]
    scenario_record_ids = {str(record["id"]) for record in scenario_observations}
    record_ids = {str(record["id"]) for record in records}
    covered_message_ids = {
        message_id
        for record in scenario_observations
        for message_id in normalize_string_list(record.get("source_message_ids", []))
        if message_id in scenario_message_ids
    }
    reflected_record_ids: set[str] = set()
    for summary in summaries:
        reflected_record_ids.update(extract_reflected_record_ids(summary))
    reflected_loaded_record_ids = reflected_record_ids.intersection(record_ids)
    scenario_distillations = [
        summary
        for summary in summaries
        if scenario_record_ids.intersection(extract_reflected_record_ids(summary))
    ]
    conversation_dialog_recorder_trace_entries = [
        entry
        for entry in dialog_recorder_trace_entries
        if str(entry.get("conversation_id", "")).strip() == conversation_id
    ]
    conversation_distiller_trace_entries = [
        entry
        for entry in distiller_trace_entries
        if scenario_record_ids.intersection(
            normalize_string_list(entry.get("source_record_ids", []))
        )
    ]
    duplication_report = {
        "duplicate_observation_count": sum(
            1
            for entry in conversation_dialog_recorder_trace_entries
            if str(entry.get("decision", "")).strip() == "duplicate_memory"
        ),
        "distillation_merge_count": sum(
            1
            for entry in conversation_distiller_trace_entries
            if str(entry.get("decision", "")).strip()
            == "summary_merged_by_existing_main_point_match"
        ),
        "stored_distillation_count": sum(
            1
            for entry in conversation_distiller_trace_entries
            if str(entry.get("decision", "")).strip()
            == "summary_stored_by_llm_synthesis"
        ),
    }
    contamination_report = build_long_term_contamination_report(scenario_distillations)

    return ScenarioState(
        conversation_id=conversation_id,
        messages=messages,
        records=scenario_observations,
        summaries=scenario_distillations,
        quality_report=quality_report,
        unreflected_record_count=(len(records) - len(reflected_loaded_record_ids)),
        scenario_unreflected_record_count=sum(
            1
            for record in scenario_observations
            if str(record["id"]) not in reflected_record_ids
        ),
        covered_message_count=len(covered_message_ids),
        message_coverage=(
            len(covered_message_ids) / len(scenario_message_ids)
            if scenario_message_ids
            else 0.0
        ),
        duplication_report=duplication_report,
        contamination_report=contamination_report,
    )


def wait_for_scenario_memory_impl(
    client: Any,
    conversation_id: str,
    *,
    expected_message_count: int,
    timeout_seconds: int,
    poll_interval_seconds: int,
    summary_trigger_threshold: int,
    required_record_count: int,
    required_message_coverage: float,
    full_scenario_message_coverage: float,
    fetch_state_fn: Any,
    monotonic_fn: Any = time.monotonic,
    sleep_fn: Any = time.sleep,
) -> ScenarioState:
    """Wait until scenario messages are stored and memory counts stabilize."""
    started_at = monotonic_fn()
    last_signature: tuple[int, int, int] | None = None
    stable_ticks = 0
    first_record_elapsed_seconds: float | None = None

    while monotonic_fn() - started_at < timeout_seconds:
        state = fetch_state_fn(client, conversation_id)
        signature = (
            len(state.messages),
            len(state.records),
            len(state.summaries),
        )
        if signature == last_signature:
            stable_ticks += 1
        else:
            stable_ticks = 0
            last_signature = signature

        elapsed_seconds = monotonic_fn() - started_at
        if first_record_elapsed_seconds is None and signature[1] > 0:
            first_record_elapsed_seconds = elapsed_seconds
        print(
            "  "
            f"[{elapsed_seconds:5.1f}s] scenario messages={signature[0]}, "
            f"records={signature[1]}, summaries={signature[2]}, "
            f"coverage={state.message_coverage:.2f}",
            flush=True,
        )

        summary_threshold_reached = (
            state.scenario_unreflected_record_count >= summary_trigger_threshold
        )
        sufficient_observations = signature[1] >= required_record_count or (
            signature[1] > 0
            and state.message_coverage >= full_scenario_message_coverage
        )
        memory_counts_stable = stable_ticks >= 2
        can_finish_with_current_state = (
            signature[0] >= expected_message_count
            and sufficient_observations
            and memory_counts_stable
            and (not summary_threshold_reached or signature[2] > 0)
        )
        if (
            can_finish_with_current_state
            and state.message_coverage >= required_message_coverage
        ):
            state.first_record_elapsed_seconds = first_record_elapsed_seconds
            state.stabilization_elapsed_seconds = elapsed_seconds
            return state
        if can_finish_with_current_state:
            state.first_record_elapsed_seconds = first_record_elapsed_seconds
            state.stabilization_elapsed_seconds = elapsed_seconds
            return state
        sleep_fn(poll_interval_seconds)

    state = fetch_state_fn(client, conversation_id)
    state.first_record_elapsed_seconds = first_record_elapsed_seconds
    state.stabilization_elapsed_seconds = monotonic_fn() - started_at
    return state


def send_scenario_dialog_steps_impl(
    client: Any,
    conversation_id: str,
    prompts: list[str],
    *,
    chat_retry_limit: int,
    rate_limit_backoff_seconds: float,
    delay_seconds: float,
    is_rate_limit_error_fn: Any,
    monotonic_fn: Any = time.monotonic,
    sleep_fn: Any = time.sleep,
) -> list[ChatDialogStepResult]:
    """Send the scripted conversation through the chat API."""
    dialog_step_results: list[ChatDialogStepResult] = []
    for index, prompt in enumerate(prompts, start=1):
        print(f"  [{index:02d}/{len(prompts):02d}] {prompt[:88]}", flush=True)
        started_at = monotonic_fn()
        last_error: Exception | None = None
        for attempt in range(1, chat_retry_limit + 1):
            try:
                response = client.post(
                    "/chat",
                    {
                        "message": prompt,
                        "conversation_id": conversation_id,
                        "images": [],
                        "file_ids": [],
                    },
                )
                elapsed_seconds = monotonic_fn() - started_at
                dialog_step_result = ChatDialogStepResult(
                    prompt=prompt,
                    response_text=str(response.get("text", "")),
                    elapsed_seconds=elapsed_seconds,
                    retrieved_memories=list(response.get("retrieved_memories") or []),
                    retrieved_messages=list(response.get("retrieved_messages") or []),
                )
                preview = dialog_step_result.response_text[:80].replace("\n", " ")
                print(f"       -> {elapsed_seconds:.1f}s | {preview}", flush=True)
                break
            except Exception as error:
                last_error = error
                if attempt < chat_retry_limit and is_rate_limit_error_fn(error):
                    print(
                        "       -> rate limited; backing off for "
                        f"{rate_limit_backoff_seconds:.0f}s",
                        flush=True,
                    )
                    sleep_fn(rate_limit_backoff_seconds)
                    continue
                elapsed_seconds = monotonic_fn() - started_at
                dialog_step_result = ChatDialogStepResult(
                    prompt=prompt,
                    response_text="",
                    elapsed_seconds=elapsed_seconds,
                    retrieved_memories=[],
                    retrieved_messages=[],
                    error=str(error),
                )
                print(
                    f"       -> ERROR after {elapsed_seconds:.1f}s | {error}",
                    flush=True,
                )
                break
        else:
            elapsed_seconds = monotonic_fn() - started_at
            dialog_step_result = ChatDialogStepResult(
                prompt=prompt,
                response_text="",
                elapsed_seconds=elapsed_seconds,
                retrieved_memories=[],
                retrieved_messages=[],
                error=str(last_error) if last_error is not None else "Unknown error",
            )
        dialog_step_results.append(dialog_step_result)
        if delay_seconds > 0 and index < len(prompts):
            sleep_fn(delay_seconds)
    return dialog_step_results
