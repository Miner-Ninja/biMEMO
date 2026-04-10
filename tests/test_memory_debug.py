"""Tests for memory debug DB Proxy handlers and event bus."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from backend.memory.dialog_recorder import trace as dialog_recorder_trace
from backend.memory.runtime.event_bus import MemoryEventBus
from backend.memory.summary_distiller import trace as distiller_trace
from backend.processes.db_proxy.handlers.admin import (
    make_count_memory_entries_handler,
    make_list_messages_handler,
    make_list_records_handler,
    make_list_summaries_full_handler,
)
from backend.processes.db_proxy.handlers.memory_debug import _build_quality_report


# --- Helpers ---


class FakeDB:
    """Minimal DB wrapper for handler tests."""

    def __init__(self) -> None:
        """Initialize an in-memory SQLite database with debug tables."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE messages ("
            "id TEXT PRIMARY KEY, role TEXT, content TEXT, "
            "conversation_id TEXT, meta TEXT DEFAULT '', "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        self.conn.execute(
            "CREATE TABLE records ("
            "id TEXT PRIMARY KEY, summary TEXT, key_facts TEXT, action_items TEXT, "
            "conversation_id TEXT, session_id TEXT, "
            "memory_kinds TEXT DEFAULT '[]', "
            "source_message_ids TEXT DEFAULT '[]', "
            "tainted INTEGER DEFAULT 0, "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        self.conn.execute(
            "CREATE TABLE summaries ("
            "id TEXT PRIMARY KEY, main_point TEXT, based_on TEXT, "
            "trust_level TEXT, summary_scope TEXT DEFAULT 'long_term', "
            "trust_level_score INTEGER DEFAULT 2, "
            "source_record_ids TEXT, "
            "record_relations TEXT DEFAULT '[]', "
            "created_at TEXT DEFAULT (datetime('now')))"
        )

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> sqlite3.Cursor:
        """Execute a SQL statement against the in-memory database."""
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        """Commit the current database transaction."""
        self.conn.commit()


@pytest.fixture
def db() -> FakeDB:
    """Provide a fresh in-memory database for each test."""
    return FakeDB()


# --- 6.1: DB Proxy handler tests ---


@pytest.mark.asyncio
async def test_list_records_empty(db: FakeDB) -> None:
    """Record listing should return an empty result by default."""
    handler = make_list_records_handler(db)
    result = await handler({"limit": 10, "offset": 0})
    assert result == {"records": []}


@pytest.mark.asyncio
async def test_list_records_with_data(db: FakeDB) -> None:
    """Record listing should return persisted record payloads."""
    record_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO records "
        "("
        "id, summary, key_facts, action_items, memory_kinds, "
        "conversation_id, tainted"
        ") "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            record_id,
            "test summary",
            json.dumps(["fact1"]),
            json.dumps(["follow up"]),
            json.dumps(["preference"]),
            "conv1",
            0,
        ),
    )
    db.commit()

    handler = make_list_records_handler(db)
    result = await handler({"limit": 10, "offset": 0})
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["id"] == record_id
    assert record["summary"] == "test summary"
    assert record["key_facts"] == ["fact1"]
    assert record["action_items"] == ["follow up"]
    assert record["memory_kinds"] == ["preference"]
    assert record["tainted"] is False


@pytest.mark.asyncio
async def test_list_summaries_full_empty(db: FakeDB) -> None:
    """Summary listing should return an empty result by default."""
    handler = make_list_summaries_full_handler(db)
    result = await handler({"limit": 10, "offset": 0})
    assert result == {"summaries": []}


@pytest.mark.asyncio
async def test_list_summaries_full_with_data(db: FakeDB) -> None:
    """Summary listing should return persisted summary payloads."""
    summary_id = str(uuid.uuid4())
    record_ids = [str(uuid.uuid4())]
    db.execute(
        "INSERT INTO records "
        "(id, summary, key_facts, action_items, conversation_id, session_id, tainted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (record_ids[0], "support", "[]", "[]", "conv1", "sess1", 0),
    )
    db.execute(
        "INSERT INTO summaries "
        "(id, main_point, based_on, trust_level, source_record_ids) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            summary_id,
            "test main point",
            json.dumps(["ev1"]),
            "high",
            json.dumps(record_ids),
        ),
    )
    db.commit()

    handler = make_list_summaries_full_handler(db)
    result = await handler({"limit": 10, "offset": 0})
    assert len(result["summaries"]) == 1
    summary = result["summaries"][0]
    assert summary["id"] == summary_id
    assert summary["main_point"] == "test main point"
    assert summary["based_on"] == ["ev1"]
    assert summary["scope"] == "long_term"
    assert summary["record_ids"] == record_ids
    assert summary["record_relations"] == [
        {"record_id": record_ids[0], "relation": "supports"}
    ]
    assert summary["revalidation_status"] == "fresh"
    assert summary["active_support_count"] == 1
    assert summary["supporting_record_count"] == 1


@pytest.mark.asyncio
async def test_list_messages_empty(db: FakeDB) -> None:
    """Message listing should return an empty result by default."""
    handler = make_list_messages_handler(db)
    result = await handler({"limit": 10, "offset": 0})
    assert result == {"messages": []}


@pytest.mark.asyncio
async def test_list_messages_with_data(db: FakeDB) -> None:
    """Message listing should return persisted message payloads."""
    message_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO messages (id, role, content, conversation_id) VALUES (?, ?, ?, ?)",
        (message_id, "user", "hello", "conv1"),
    )
    db.commit()

    handler = make_list_messages_handler(db)
    result = await handler({"limit": 10, "offset": 0})
    assert len(result["messages"]) == 1
    message = result["messages"][0]
    assert message["id"] == message_id
    assert message["role"] == "user"
    assert message["content"] == "hello"


@pytest.mark.asyncio
async def test_list_messages_filter_by_conversation(db: FakeDB) -> None:
    """Message listing should filter by conversation identifier."""
    db.execute(
        "INSERT INTO messages (id, role, content, conversation_id) VALUES (?, ?, ?, ?)",
        ("m1", "user", "msg1", "conv1"),
    )
    db.execute(
        "INSERT INTO messages (id, role, content, conversation_id) VALUES (?, ?, ?, ?)",
        ("m2", "user", "msg2", "conv2"),
    )
    db.commit()

    handler = make_list_messages_handler(db)
    result = await handler({"limit": 10, "offset": 0, "conversation_id": "conv1"})
    assert len(result["messages"]) == 1
    assert result["messages"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_count_memory_entries(db: FakeDB) -> None:
    """Memory counters should reflect the persisted row totals."""
    db.execute(
        "INSERT INTO messages (id, role, content, conversation_id) VALUES (?, ?, ?, ?)",
        ("m1", "user", "hello", "c1"),
    )
    db.execute(
        "INSERT INTO records (id, summary, key_facts, conversation_id) "
        "VALUES (?, ?, ?, ?)",
        ("o1", "sum", "[]", "c1"),
    )
    db.commit()

    handler = make_count_memory_entries_handler(db)
    result = await handler({})
    assert result["messages"] == 1
    assert result["records"] == 1
    assert result["summaries"] == 0


def test_build_quality_report_flags_missing_provenance_and_orphaned_distillation() -> (
    None
):
    """Quality report should surface weak provenance and orphaned summary support."""
    report = _build_quality_report(
        messages=[
            {"id": "m1", "content": "msg"},
        ],
        records=[
            {
                "id": "o1",
                "summary": "prefers concise replies",
                "key_facts": ["likes concise output"],
                "action_items": [],
                "source_message_ids": [],
            },
        ],
        summaries=[
            {
                "id": "r1",
                "main_point": "prefers concise replies",
                "based_on": ["likes concise output"],
                "record_ids": ["missing-record"],
                "revalidation_status": "orphaned",
            },
            {
                "id": "r2",
                "main_point": "prefers concise replies",
                "based_on": ["likes concise output"],
                "record_ids": ["missing-record"],
                "revalidation_status": "orphaned",
            },
        ],
    )

    assert report["quality_score"] < 70
    assert report["records"]["with_provenance"] == 0
    assert report["summaries"]["duplicate_main_points"] == 1
    assert report["summaries"]["status_breakdown"]["orphaned"] == 2
    assert any(
        "missing source message provenance" in issue for issue in report["issues"]
    )
    assert any("lost all linked records" in issue for issue in report["issues"])


def test_build_quality_report_rewards_well_linked_memory() -> None:
    """Quality report should score healthy memory chains highly."""
    report = _build_quality_report(
        messages=[
            {"id": "m1", "content": "prefers concise answers"},
        ],
        records=[
            {
                "id": "o1",
                "summary": "prefers concise answers",
                "key_facts": ["asks for concise output"],
                "action_items": ["keep answers short"],
                "source_message_ids": ["m1"],
            },
        ],
        summaries=[
            {
                "id": "r1",
                "main_point": "user prefers concise answers",
                "based_on": ["asks for concise output"],
                "record_ids": ["o1"],
                "revalidation_status": "fresh",
            },
        ],
    )

    assert report["quality_score"] >= 90
    assert report["issues"] == []


def test_build_quality_report_counts_outdated_distillations_as_useful_memory() -> None:
    """Outdated summaries should still contribute when they are well linked."""
    report = _build_quality_report(
        messages=[
            {"id": "m1", "content": "prefers concise answers"},
            {"id": "m2", "content": "now prefers detailed answers"},
        ],
        records=[
            {
                "id": "o1",
                "summary": "prefers concise answers",
                "key_facts": ["asks for concise output"],
                "action_items": ["keep answers short"],
                "source_message_ids": ["m1"],
            },
            {
                "id": "o2",
                "summary": "now prefers detailed answers",
                "key_facts": ["asks for step-by-step output"],
                "action_items": ["answer in detail"],
                "source_message_ids": ["m2"],
            },
        ],
        summaries=[
            {
                "id": "r1",
                "main_point": "user prefers concise answers",
                "based_on": ["asks for concise output"],
                "record_ids": ["o1"],
                "revalidation_status": "outdated",
            },
            {
                "id": "r2",
                "main_point": "user now prefers detailed answers",
                "based_on": ["asks for step-by-step output"],
                "record_ids": ["o2"],
                "revalidation_status": "fresh",
            },
        ],
    )

    assert report["summaries"]["status_breakdown"]["outdated"] == 1
    assert report["quality_score"] >= 90


def test_dialog_recorder_trace_roundtrip_reads_newest_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dialog recorder trace should append entries and return newest-first slices."""
    trace_path = tmp_path / "dialog-recorder-trace.jsonl"
    monkeypatch.setattr(dialog_recorder_trace, "TRACE_FILE_PATH", trace_path)

    dialog_recorder_trace.append_dialog_recorder_trace(
        {
            "decision": "record_stored_by_llm_memory_judgment",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "messages": ["first"],
            "source_message_ids": ["m1"],
            "message_count": 1,
        }
    )
    dialog_recorder_trace.append_dialog_recorder_trace(
        {
            "decision": "record_rejected_by_duplicate_check",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "messages": ["second"],
            "source_message_ids": ["m2"],
            "message_count": 1,
        }
    )

    entries = dialog_recorder_trace.read_dialog_recorder_trace(limit=2)

    assert [entry["decision"] for entry in entries] == [
        "record_rejected_by_duplicate_check",
        "record_stored_by_llm_memory_judgment",
    ]
    assert all("timestamp" in entry for entry in entries)


def test_dialog_recorder_trace_filters_by_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dialog recorder trace reader should filter entries by decision."""
    trace_path = tmp_path / "dialog-recorder-trace.jsonl"
    monkeypatch.setattr(dialog_recorder_trace, "TRACE_FILE_PATH", trace_path)
    dialog_recorder_trace.append_dialog_recorder_trace(
        {
            "decision": "record_stored_by_llm_memory_judgment",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "messages": ["keep"],
            "source_message_ids": ["m1"],
            "message_count": 1,
        }
    )
    dialog_recorder_trace.append_dialog_recorder_trace(
        {
            "decision": "record_skipped_by_rate_limit",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "messages": ["skip"],
            "source_message_ids": ["m2"],
            "message_count": 1,
        }
    )

    entries = dialog_recorder_trace.read_dialog_recorder_trace(
        limit=10,
        decision="record_stored_by_llm_memory_judgment",
    )

    assert len(entries) == 1
    assert entries[0]["decision"] == "record_stored_by_llm_memory_judgment"


def test_distiller_trace_roundtrip_reads_newest_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Distiller trace should append entries and return newest-first slices."""
    trace_path = tmp_path / "distiller-trace.jsonl"
    monkeypatch.setattr(distiller_trace, "TRACE_FILE_PATH", trace_path)

    distiller_trace.append_distiller_trace(
        {
            "decision": "summary_stored_by_llm_synthesis",
            "record_count": 2,
            "source_record_ids": ["o1", "o2"],
        }
    )
    distiller_trace.append_distiller_trace(
        {
            "decision": "summary_skipped_by_llm_output_parse_failure",
            "record_count": 1,
            "source_record_ids": ["o3"],
        }
    )

    entries = distiller_trace.read_distiller_trace(limit=2)

    assert [entry["decision"] for entry in entries] == [
        "summary_skipped_by_llm_output_parse_failure",
        "summary_stored_by_llm_synthesis",
    ]
    assert all("timestamp" in entry for entry in entries)


def test_distiller_trace_filters_by_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Distiller trace reader should filter entries by decision."""
    trace_path = tmp_path / "distiller-trace.jsonl"
    monkeypatch.setattr(distiller_trace, "TRACE_FILE_PATH", trace_path)
    distiller_trace.append_distiller_trace(
        {
            "decision": "summary_stored_by_llm_synthesis",
            "record_count": 2,
            "source_record_ids": ["o1", "o2"],
        }
    )
    distiller_trace.append_distiller_trace(
        {
            "decision": "summary_rejected_by_output_filter",
            "record_count": 1,
            "source_record_ids": ["o3"],
        }
    )

    entries = distiller_trace.read_distiller_trace(
        limit=10,
        decision="summary_stored_by_llm_synthesis",
    )

    assert len(entries) == 1
    assert entries[0]["decision"] == "summary_stored_by_llm_synthesis"


# --- 6.2: MemoryEventBus tests ---


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe() -> None:
    """Published events should reach a subscribed queue."""
    bus = MemoryEventBus()
    subscriber_id, queue = bus.subscribe()

    await bus.publish({"type": "dialog_recorder", "summary": "test"})
    event = queue.get_nowait()
    assert event["type"] == "dialog_recorder"
    assert event["summary"] == "test"
    assert "timestamp" in event

    bus.unsubscribe(subscriber_id)


@pytest.mark.asyncio
async def test_event_bus_fan_out() -> None:
    """Published events should fan out to every subscriber queue."""
    bus = MemoryEventBus()
    _, first_queue = bus.subscribe()
    _, second_queue = bus.subscribe()

    await bus.publish({"type": "distiller"})
    assert first_queue.get_nowait()["type"] == "distiller"
    assert second_queue.get_nowait()["type"] == "distiller"


@pytest.mark.asyncio
async def test_event_bus_overflow() -> None:
    """Overflow publishing should not raise once the queue is full."""
    bus = MemoryEventBus()
    subscriber_id, queue = bus.subscribe()

    # Fill the queue
    for i in range(1000):
        await bus.publish({"type": "test", "i": i})

    # Next publish should not raise
    await bus.publish({"type": "overflow"})

    assert queue.qsize() == 1000
    bus.unsubscribe(subscriber_id)


@pytest.mark.asyncio
async def test_event_bus_unsubscribe() -> None:
    """Unsubscribed queues should stop receiving new events."""
    bus = MemoryEventBus()
    subscriber_id, queue = bus.subscribe()
    assert bus.subscriber_count == 1

    bus.unsubscribe(subscriber_id)
    assert bus.subscriber_count == 0

    # Publishing after unsubscribe should not error
    await bus.publish({"type": "test"})
    assert queue.empty()
