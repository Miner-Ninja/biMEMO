"""Contract tests for core-authority structured agent turns."""

import hashlib
import json
import os

import pytest

from backend.processes.db_proxy.handlers.admin import (
    make_record_rate_limit_call_handler,
)
from backend.processes.db_proxy.handlers.agent_turn import (
    make_append_agent_turn_event_handler,
    make_cancel_agent_turn_handler,
    make_claim_agent_invocation_handler,
    make_commit_agent_turn_finalization_handler,
    make_complete_agent_invocation_handler,
    make_create_agent_clarification_handler,
    make_create_agent_turn_handler,
    make_decide_agent_clarification_handler,
    make_get_agent_turn_handler,
    make_get_pending_agent_clarification_handler,
    make_list_agent_turn_events_handler,
    make_list_unprojected_agent_turn_events_handler,
    make_mark_agent_turn_event_projected_handler,
    make_recover_agent_turns_handler,
    make_upsert_agent_tool_batch_handler,
    make_upsert_agent_turn_obligations_handler,
)
from backend.processes.db_proxy.handlers.memory import (
    make_delete_queued_records_handler,
)
from backend.processes.db_proxy.handlers.permissions import (
    make_project_agent_turn_event_handler,
)
from backend.security.database import DatabaseManager
from backend.security.runtime_schema import initialize_runtime_schema
from backend.security.schema import initialize_schema


def _open_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "agent-turn.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    return db


def _args_hash(arguments: dict[str, object]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def test_rate_limit_write_closes_before_next_transaction(tmp_path) -> None:
    """Persistent tool accounting cannot poison invocation persistence."""
    db = _open_db(tmp_path)
    handler = make_record_rate_limit_call_handler(db)

    await handler({"tool_name": "web_search", "called_at": 1_000.0})

    assert db.in_transaction is False
    db.execute("BEGIN IMMEDIATE")
    db.rollback()


async def test_turn_pins_tool_policy_version_and_replay_identity(tmp_path) -> None:
    """A turn keeps its permission architecture across restart and replay."""
    db = _open_db(tmp_path)
    create = make_create_agent_turn_handler(db)
    payload = {
        "turn_id": "policy-turn",
        "conversation_id": "conversation-1",
        "session_id": "session-1",
        "channel": "web",
        "tool_policy_version": "registry-v1",
    }

    first = await create(payload)
    replay = await create(payload)

    assert first["turn"]["tool_policy_version"] == "registry-v1"
    assert replay["turn"]["finalization_id"] == first["turn"]["finalization_id"]
    with pytest.raises(ValueError, match="registry-v1"):
        await create({**payload, "tool_policy_version": "legacy"})


async def _prepared_turn(db: DatabaseManager) -> tuple[str, str, str]:
    turn_id = "turn-1"
    finalization_id = "final-1"
    obligation_id = "obligation-1"
    await make_create_agent_turn_handler(db)(
        {
            "turn_id": turn_id,
            "finalization_id": finalization_id,
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "channel": "web",
        }
    )
    await make_upsert_agent_turn_obligations_handler(db)(
        {
            "turn_id": turn_id,
            "obligations": [
                {
                    "obligation_id": obligation_id,
                    "required_tool": "http_fetch",
                    "source": "explicit_url",
                    "evidence_key": "url:https://example.test",
                    "argument_predicate": {"url": "https://example.test"},
                }
            ],
        }
    )
    return turn_id, finalization_id, obligation_id


async def test_success_binds_only_after_durable_execution_claim(tmp_path):
    """Candidate/name records do not satisfy an obligation before success."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, obligation_id = await _prepared_turn(db)
    invocation_id = "invocation-1"
    await make_upsert_agent_tool_batch_handler(db)(
        {
            "turn_id": turn_id,
            "batch_id": "batch-1",
            "assistant_message": {
                "role": "assistant",
                "tool_calls": [{"id": "call-1"}],
            },
            "transcript_snapshot": [{"role": "user", "content": "fetch it"}],
            "invocations": [
                {
                    "invocation_id": invocation_id,
                    "tool_call_id": "call-1",
                    "tool_name": "http_fetch",
                    "arguments": {"url": "https://example.test"},
                    "canonical_args_hash": _args_hash({"url": "https://example.test"}),
                    "policy_revision": "registry-v1",
                    "candidate_obligation_ids": [obligation_id],
                }
            ],
        }
    )
    get_turn = make_get_agent_turn_handler(db)
    before = (await get_turn({"turn_id": turn_id}))["turn"]
    assert before["binds"] == []
    assert before["obligations"][0]["status"] == "open"

    claim = await make_claim_agent_invocation_handler(db)(
        {
            "turn_id": turn_id,
            "invocation_id": invocation_id,
            "idempotency_key": "dispatch-1",
        }
    )
    completed = await make_complete_agent_invocation_handler(db)(
        {
            "turn_id": turn_id,
            "invocation_id": invocation_id,
            "claim_generation": claim["claim_generation"],
            "status": "succeeded",
            "result": {"data": "evidence"},
        }
    )
    assert completed["bound"] == [obligation_id]
    after = (await get_turn({"turn_id": turn_id}))["turn"]
    assert after["obligations"][0]["status"] == "satisfied"
    assert after["binds"][0]["invocation_id"] == invocation_id
    db.close()


async def test_scheduler_finalization_is_idempotent_with_one_final_row(tmp_path):
    """A successful scheduled turn persists exactly one accepted assistant final."""
    db = _open_db(tmp_path)
    turn_id, finalization_id, _obligation_id = await _prepared_turn(db)
    handler = make_commit_agent_turn_finalization_handler(db)
    payload = {
        "structured_content": {"answer": "done", "suggestions": []},
        "stats": None,
    }
    params = {
        "turn_id": turn_id,
        "finalization_id": finalization_id,
        "outcome_kind": "final",
        "conversation_id": "conversation-1",
        "session_id": "sched-task-1",
        "assistant_content": "done",
        "assistant_meta": None,
        "dialog_content": "ignored",
        "source_message_ids": [],
        "outcome_payload": payload,
        "persist_chat": True,
    }
    first = await handler(params)
    replay = await handler(params)
    assert first["assistant_message_id"] is not None
    assert replay["replayed"] is True
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM record_queue").fetchone()[0] == 1
    assert (
        db.execute("SELECT COUNT(*) FROM agent_turn_finalizations").fetchone()[0] == 1
    )

    with pytest.raises(ValueError, match="replay payload mismatch"):
        await handler({**params, "outcome_payload": {**payload, "extra": True}})
    db.close()


async def test_processed_dialog_queue_does_not_poison_next_tool_transaction(tmp_path):
    """Acknowledging a finalized dialog queue leaves the core DB transaction clean."""
    db = _open_db(tmp_path)
    db.execute(
        "INSERT INTO conversations (id, title) VALUES (?, ?)",
        ("conversation-1", "Transaction regression"),
    )
    db.commit()
    turn_id, finalization_id, obligation_id = await _prepared_turn(db)
    committed = await make_commit_agent_turn_finalization_handler(db)(
        {
            "turn_id": turn_id,
            "finalization_id": finalization_id,
            "outcome_kind": "final",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "assistant_content": "done",
            "assistant_meta": None,
            "dialog_content": "User: hello\nAssistant: done",
            "source_message_ids": [],
            "outcome_payload": {
                "structured_content": {"answer": "done", "suggestions": []}
            },
            "persist_chat": True,
        }
    )
    queue_id = committed["record_queue_id"]

    await make_delete_queued_records_handler(db)({"queue_ids": [queue_id]})

    assert db.execute("SELECT COUNT(*) FROM record_queue").fetchone()[0] == 0
    assert (
        db.execute(
            "SELECT record_queue_id FROM agent_turn_finalizations WHERE id = ?",
            (finalization_id,),
        ).fetchone()[0]
        is None
    )
    db.execute("BEGIN IMMEDIATE")
    db.execute("ROLLBACK")
    assert obligation_id
    db.close()


async def test_scheduler_failure_has_no_chat_rows(tmp_path):
    """A terminal scheduled failure stores its outcome without chat persistence."""
    db = _open_db(tmp_path)
    turn_id, finalization_id, _obligation_id = await _prepared_turn(db)
    await make_commit_agent_turn_finalization_handler(db)(
        {
            "turn_id": turn_id,
            "finalization_id": finalization_id,
            "outcome_kind": "failed",
            "conversation_id": "conversation-1",
            "session_id": "sched-task-1",
            "assistant_content": "failed",
            "assistant_meta": None,
            "dialog_content": "ignored",
            "source_message_ids": [],
            "outcome_payload": {
                "structured_content": {"answer": "failed", "suggestions": []}
            },
            "persist_chat": False,
        }
    )
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM record_queue").fetchone()[0] == 0
    assert (
        db.execute("SELECT COUNT(*) FROM agent_turn_finalizations").fetchone()[0] == 1
    )
    db.close()


async def test_scheduler_task_result_commits_atomically_and_replays_once(tmp_path):
    """Terminal state and scheduler history share one idempotent transaction."""
    db = _open_db(tmp_path)
    db.execute(
        "INSERT INTO scheduled_tasks "
        "(id, name, prompt, task_type, schedule_kind, schedule_expr, "
        "next_run_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task-1",
            "Atomic task",
            "Do work",
            "agent_task",
            "once",
            "",
            "2026-01-01T00:00:00+00:00",
            "active",
        ),
    )
    db.commit()
    turn_id, finalization_id, _obligation_id = await _prepared_turn(db)
    handler = make_commit_agent_turn_finalization_handler(db)
    params = {
        "turn_id": turn_id,
        "finalization_id": finalization_id,
        "outcome_kind": "failed",
        "conversation_id": "conversation-1",
        "session_id": "sched-task-1",
        "assistant_content": "failed",
        "assistant_meta": None,
        "dialog_content": "",
        "source_message_ids": [],
        "outcome_payload": {
            "structured_content": {"answer": "failed", "suggestions": []}
        },
        "persist_chat": False,
        "task_result": {
            "task_id": "task-1",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
            "status": "error",
            "result_text": "",
            "error_message": "failed",
            "duration_ms": 1000,
        },
    }

    await handler(params)
    replay = await handler(params)

    assert replay["replayed"] is True
    assert db.execute("SELECT COUNT(*) FROM scheduled_task_runs").fetchone()[0] == 1
    row = db.execute(
        "SELECT id, status, error_message FROM scheduled_task_runs"
    ).fetchone()
    assert row == (finalization_id, "error", "failed")
    turn = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert turn["terminal_outcome"] == "failed"
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    db.close()


async def test_scheduler_timeout_fails_turn_and_records_one_result(tmp_path):
    """A pre-final timeout closes the core turn and task history atomically."""
    db = _open_db(tmp_path)
    db.execute(
        "INSERT INTO scheduled_tasks "
        "(id, name, prompt, task_type, schedule_kind, schedule_expr, "
        "next_run_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task-timeout",
            "Timeout task",
            "Do work",
            "agent_task",
            "once",
            "",
            "2026-01-01T00:00:00+00:00",
            "active",
        ),
    )
    db.commit()
    turn_id, finalization_id, _obligation_id = await _prepared_turn(db)

    result = await make_cancel_agent_turn_handler(db)(
        {
            "turn_id": turn_id,
            "reason": "Timed out.",
            "timeout": True,
            "task_result": {
                "task_id": "task-timeout",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:00:02+00:00",
                "status": "timeout",
                "result_text": "",
                "error_message": "Timed out.",
                "duration_ms": 2000,
            },
        }
    )

    assert result["won"] is True
    assert result["turn"]["terminal_outcome"] == "failed"
    row = db.execute(
        "SELECT id, status, error_message FROM scheduled_task_runs"
    ).fetchone()
    assert row == (finalization_id, "timeout", "Timed out.")
    assert db.execute("SELECT COUNT(*) FROM scheduled_task_runs").fetchone()[0] == 1
    db.close()


async def test_batch_replay_rejects_changed_arguments(tmp_path):
    """Stable invocation IDs cannot be replayed with different side effects."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, obligation_id = await _prepared_turn(db)
    handler = make_upsert_agent_tool_batch_handler(db)
    base = {
        "turn_id": turn_id,
        "batch_id": "batch-1",
        "assistant_message": {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
        "transcript_snapshot": [],
        "invocations": [
            {
                "invocation_id": "invocation-1",
                "tool_call_id": "call-1",
                "tool_name": "http_fetch",
                "arguments": {"url": "https://example.test"},
                "canonical_args_hash": _args_hash({"url": "https://example.test"}),
                "policy_revision": "registry-v1",
                "candidate_obligation_ids": [obligation_id],
            }
        ],
    }
    await handler(base)
    changed = {
        **base,
        "invocations": [
            {
                **base["invocations"][0],
                "arguments": {"url": "https://other.test"},
                "canonical_args_hash": _args_hash({"url": "https://other.test"}),
            }
        ],
    }
    with pytest.raises(ValueError, match="invocation replay payload mismatch"):
        await handler(changed)
    db.close()


async def test_late_callback_after_cancel_is_audited_without_binding(tmp_path):
    """A matching late callback remains fenced UNKNOWN and cannot satisfy evidence."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, obligation_id = await _prepared_turn(db)
    invocation_id = "invocation-late"
    await make_upsert_agent_tool_batch_handler(db)(
        {
            "turn_id": turn_id,
            "batch_id": "batch-late",
            "assistant_message": {"role": "assistant", "tool_calls": []},
            "transcript_snapshot": [],
            "invocations": [
                {
                    "invocation_id": invocation_id,
                    "tool_call_id": "call-late",
                    "tool_name": "http_fetch",
                    "arguments": {"url": "https://example.test"},
                    "canonical_args_hash": _args_hash({"url": "https://example.test"}),
                    "policy_revision": "registry-v1",
                    "candidate_obligation_ids": [obligation_id],
                }
            ],
        }
    )
    claim = await make_claim_agent_invocation_handler(db)(
        {
            "turn_id": turn_id,
            "invocation_id": invocation_id,
            "idempotency_key": "dispatch-late",
        }
    )
    await make_cancel_agent_turn_handler(db)(
        {"turn_id": turn_id, "reason": "stopped", "timeout": False}
    )
    completed = await make_complete_agent_invocation_handler(db)(
        {
            "turn_id": turn_id,
            "invocation_id": invocation_id,
            "claim_generation": claim["claim_generation"],
            "status": "succeeded",
            "result": {"data": "late evidence"},
        }
    )
    assert completed == {"stored": True, "status": "unknown", "bound": []}
    turn = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert turn["terminal_outcome"] == "cancelled"
    assert turn["invocations"][0]["status"] == "unknown"
    assert turn["invocations"][0]["result"] == {"data": "late evidence"}
    assert turn["binds"] == []
    db.close()


async def test_restart_fences_abandoned_execution_claim(tmp_path):
    """Startup recovery makes an uncertain claim UNKNOWN without redispatch."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, obligation_id = await _prepared_turn(db)
    invocation_id = "invocation-crash"
    await make_upsert_agent_tool_batch_handler(db)(
        {
            "turn_id": turn_id,
            "batch_id": "batch-crash",
            "assistant_message": {"role": "assistant", "tool_calls": []},
            "transcript_snapshot": [],
            "invocations": [
                {
                    "invocation_id": invocation_id,
                    "tool_call_id": "call-crash",
                    "tool_name": "http_fetch",
                    "arguments": {"url": "https://example.test"},
                    "canonical_args_hash": _args_hash({"url": "https://example.test"}),
                    "policy_revision": "registry-v1",
                    "candidate_obligation_ids": [obligation_id],
                }
            ],
        }
    )
    claim = await make_claim_agent_invocation_handler(db)(
        {
            "turn_id": turn_id,
            "invocation_id": invocation_id,
            "idempotency_key": "dispatch-crash",
        }
    )
    assert claim["claimed"] is True

    recovered = await make_recover_agent_turns_handler(db)({})
    assert recovered == {"recovered": 1}
    turn = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert turn["invocations"][0]["status"] == "unknown"
    assert turn["binds"] == []
    assert turn["events"][-1]["event_type"] == "invocation_fenced_unknown"

    with pytest.raises(RuntimeError, match="cannot be claimed from unknown"):
        await make_claim_agent_invocation_handler(db)(
            {
                "turn_id": turn_id,
                "invocation_id": invocation_id,
                "idempotency_key": "dispatch-crash",
            }
        )
    db.close()


async def test_foreground_counters_are_durable_events(tmp_path):
    """Tool, reask, provider, and synthesis attempts survive process restart."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    append = make_append_agent_turn_event_handler(db)
    await append(
        {
            "turn_id": turn_id,
            "event_type": "tool_round_started",
            "phase": "tool_loop",
            "attempt": 1,
            "payload": {},
        }
    )
    await append(
        {
            "turn_id": turn_id,
            "event_type": "active_time_recorded",
            "phase": "foreground",
            "attempt": 0,
            "payload": {"duration_ms": 125},
        }
    )
    await append(
        {
            "turn_id": turn_id,
            "event_type": "exact_force_ignored",
            "phase": "tool_loop",
            "attempt": 1,
            "payload": {},
        }
    )
    await append(
        {
            "turn_id": turn_id,
            "event_type": "provider_attempt",
            "phase": "Stage 2",
            "attempt": 1,
            "payload": {"error": ""},
        }
    )
    turn = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert turn["tool_rounds"] == 1
    assert turn["enforcement_reasks"] == 1
    assert turn["provider_calls"] == 1
    assert turn["synthesis_attempts"] == 1
    assert turn["active_time_ms"] == 125
    db.close()


async def test_reconnect_reads_core_events_by_conversation(tmp_path):
    """Runtime pruning cannot hide canonical pause events from reconnect."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    await make_append_agent_turn_event_handler(db)(
        {
            "turn_id": turn_id,
            "event_type": "run_paused",
            "phase": "confirmation",
            "attempt": 0,
            "payload": {"pending_confirmations": [{"action_id": "confirm-1"}]},
        }
    )
    events = await make_list_agent_turn_events_handler(db)(
        {"conversation_id": "conversation-1", "limit": 20}
    )
    assert len(events["events"]) == 1
    assert events["events"][0]["run_id"] == turn_id
    assert events["events"][0]["event_type"] == "run_paused"
    db.close()


async def test_reconnect_reads_latest_core_event_tail(tmp_path):
    """A long conversation cannot hide its newest pause behind the page limit."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    append = make_append_agent_turn_event_handler(db)
    for attempt in range(105):
        await append(
            {
                "turn_id": turn_id,
                "event_type": "provider_attempt",
                "phase": "Stage 1",
                "attempt": attempt,
                "payload": {},
            }
        )
    await append(
        {
            "turn_id": turn_id,
            "event_type": "run_paused",
            "phase": "confirmation",
            "attempt": 0,
            "payload": {"pending_confirmations": [{"action_id": "latest"}]},
        }
    )

    events = await make_list_agent_turn_events_handler(db)(
        {"conversation_id": "conversation-1", "limit": 100}
    )

    assert len(events["events"]) == 100
    assert events["events"][-1]["event_type"] == "run_paused"
    assert events["events"][-1]["payload"]["pending_confirmations"] == [
        {"action_id": "latest"}
    ]
    db.close()


async def test_core_event_projection_repair_is_idempotent(tmp_path):
    """A projection crash can replay a stable core event without duplication."""
    core = _open_db(tmp_path)
    runtime = DatabaseManager(str(tmp_path / "runtime.db"))
    runtime.open(os.urandom(32))
    initialize_runtime_schema(runtime)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(core)
    await make_append_agent_turn_event_handler(core)(
        {
            "turn_id": turn_id,
            "event_type": "run_paused",
            "phase": "confirmation",
            "attempt": 0,
            "payload": {"pending_confirmations": [{"action_id": "confirm-1"}]},
        }
    )
    event = (
        await make_list_unprojected_agent_turn_events_handler(core)({"limit": 10})
    )["events"][-1]
    params = {
        "event_id": event["event_id"],
        "turn_id": event["turn_id"],
        "conversation_id": event["conversation_id"],
        "session_id": event["session_id"],
        "channel": event["channel"],
        "sequence": event["sequence"],
        "event_type": event["event_type"],
        "payload": event["payload"],
        "created_at": event["created_at"],
    }

    project = make_project_agent_turn_event_handler(runtime)
    first = await project(params)
    replay = await project(params)
    assert first == {"projected": True}
    assert replay == {"projected": False}
    assert runtime.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1
    assert runtime.execute("SELECT COUNT(*) FROM agent_run_events").fetchone()[0] == 1

    marked = await make_mark_agent_turn_event_projected_handler(core)(
        {"event_id": event["event_id"], "projection_version": 1}
    )
    assert marked == {"updated": True}
    remaining = await make_list_unprojected_agent_turn_events_handler(core)(
        {"limit": 10}
    )
    assert all(item["event_id"] != event["event_id"] for item in remaining["events"])
    runtime.close()
    core.close()


async def test_clarification_is_durable_and_closes_once(tmp_path):
    """A proposal survives reads and a classified answer closes it by CAS."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    created = await make_create_agent_clarification_handler(db)(
        {
            "turn_id": turn_id,
            "clarification_id": "clarification-1",
            "proposed_tool": "web_search",
            "reason": "current data",
            "expires_at": "2099-01-01T00:00:00.000",
        }
    )
    assert created["status"] == "pending"
    pending = await make_get_pending_agent_clarification_handler(db)(
        {"conversation_id": "conversation-1"}
    )
    assert pending["clarification"]["proposed_tool"] == "web_search"

    decided = await make_decide_agent_clarification_handler(db)(
        {"clarification_id": "clarification-1", "decision": "accepted"}
    )
    assert decided["changed"] is True
    replay = await make_decide_agent_clarification_handler(db)(
        {"clarification_id": "clarification-1", "decision": "accepted"}
    )
    assert replay["changed"] is False
    assert (
        await make_get_pending_agent_clarification_handler(db)(
            {"conversation_id": "conversation-1"}
        )
    )["clarification"] is None
    db.close()


def _confirmation_batch_params(turn_id: str) -> dict:
    """Build one deterministic batch carrying a red confirmation."""
    return {
        "turn_id": turn_id,
        "batch_id": "batch-replay",
        "assistant_message": {
            "role": "assistant",
            "tool_calls": [{"id": "call-red"}],
        },
        "transcript_snapshot": [{"role": "user", "content": "run it"}],
        "invocations": [
            {
                "invocation_id": "invocation-red",
                "tool_call_id": "call-red",
                "tool_name": "execute_command",
                "arguments": {"command": "ls", "cwd": "/tmp"},
                "canonical_args_hash": _args_hash({"command": "ls", "cwd": "/tmp"}),
                "policy_revision": "registry-v1",
                "confirmation_id": "confirm-replay",
                "confirmation_title": "Run ls?",
                "confirmation_description": "ls in /tmp",
                "confirmation_expires_at": "2099-01-01T00:00:00.000",
            }
        ],
    }


async def _count_run_paused(db, turn_id: str) -> int:
    events = await make_list_agent_turn_events_handler(db)(
        {"turn_id": turn_id, "limit": 100}
    )
    return sum(1 for event in events["events"] if event["event_type"] == "run_paused")


async def test_batch_replay_does_not_duplicate_run_paused(tmp_path):
    """Replaying the same confirmation batch keeps exactly one pause event."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    upsert = make_upsert_agent_tool_batch_handler(db)

    await upsert(_confirmation_batch_params(turn_id))
    assert await _count_run_paused(db, turn_id) == 1

    await upsert(_confirmation_batch_params(turn_id))
    await upsert(_confirmation_batch_params(turn_id))
    assert await _count_run_paused(db, turn_id) == 1

    turn = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert turn["terminal_outcome"] == "paused"
    db.close()


async def test_batch_replay_does_not_repause_advanced_turn(tmp_path):
    """A replay after the confirmation was decided cannot re-pause the turn."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    upsert = make_upsert_agent_tool_batch_handler(db)
    await upsert(_confirmation_batch_params(turn_id))

    # Mirror the resume transition (status='tool_loop', outcome cleared)
    # that follows an approved confirmation.
    db.execute(
        "UPDATE agent_turns SET status = 'tool_loop', terminal_outcome = NULL "
        "WHERE id = ? AND terminal_outcome = 'paused'",
        (turn_id,),
    )
    db.commit()
    advanced = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert advanced["terminal_outcome"] is None

    await upsert(_confirmation_batch_params(turn_id))

    after = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert after["terminal_outcome"] is None
    assert after["status"] == "tool_loop"
    assert await _count_run_paused(db, turn_id) == 1
    db.close()


async def test_replayed_event_does_not_double_count_metrics(tmp_path):
    """A replayed provider_attempt (stable event_id) counts exactly once."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    append = make_append_agent_turn_event_handler(db)
    event = {
        "turn_id": turn_id,
        "event_id": "attempt-replay-1",
        "event_type": "provider_attempt",
        "phase": "Stage 1",
        "attempt": 1,
        "payload": {"error": ""},
    }

    first = await append(event)
    second = await append(event)

    assert first["event_id"] == second["event_id"] == "attempt-replay-1"
    turn = (await make_get_agent_turn_handler(db)({"turn_id": turn_id}))["turn"]
    assert turn["provider_calls"] == 1
    db.close()


async def test_list_agent_confirmations_returns_all_statuses(tmp_path):
    """The trace read exposes decided cards too, not only pending ones."""
    db = _open_db(tmp_path)
    turn_id, _finalization_id, _obligation_id = await _prepared_turn(db)
    await make_upsert_agent_tool_batch_handler(db)(_confirmation_batch_params(turn_id))
    from backend.processes.db_proxy.handlers.agent_turn import (
        make_decide_agent_confirmation_handler,
        make_list_agent_confirmations_handler,
    )

    await make_decide_agent_confirmation_handler(db)(
        {"confirmation_id": "confirm-replay", "decision": "denied"}
    )
    listed = await make_list_agent_confirmations_handler(db)({"turn_id": turn_id})
    assert len(listed["confirmations"]) == 1
    row = listed["confirmations"][0]
    assert row["confirmation_id"] == "confirm-replay"
    assert row["status"] == "denied"
    assert row["tool_name"] == "execute_command"
    assert row["arguments"] == {"command": "ls", "cwd": "/tmp"}
