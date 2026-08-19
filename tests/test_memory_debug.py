"""Tests for memory debug DB Proxy handlers and event bus."""

import json
import sqlite3
import uuid
from typing import TYPE_CHECKING

import pytest

from backend.memory.dialog_recorder import trace as dialog_recorder_trace
from backend.memory.runtime.event_bus import MemoryEventBus
from backend.processes.db_proxy.handlers.admin import (
    make_count_memory_entries_handler,
    make_list_messages_handler,
    make_list_records_handler,
)


if TYPE_CHECKING:
    from pathlib import Path


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
            "CREATE TABLE assertions ("
            "id TEXT PRIMARY KEY, subject_entity_id TEXT, relation TEXT, "
            "assertion_text TEXT, trust_level TEXT, valid_from TEXT, "
            "invalid_from TEXT, source_record_ids TEXT DEFAULT '[]', "
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
    assert result["assertions"] == 0


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
