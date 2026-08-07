"""Tests for the task scheduler subsystem.

Covers schedule parsing (once, interval, cron, invalid), DB handlers
(create, list, get_due, advance, mark_run, delete), missed task
protection, scheduler loop logic, tool allowlist filtering, and
the schedule_task LLM tool.
"""

import sqlite3
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.processes.db_proxy.handlers.scheduler import (
    make_advance_task_next_run_handler,
    make_create_scheduled_task_handler,
    make_delete_scheduled_task_handler,
    make_get_due_tasks_handler,
    make_list_scheduled_tasks_handler,
    make_list_task_runs_handler,
    make_mark_task_run_handler,
    make_record_task_run_handler,
)
from backend.scheduler.missed import compute_fast_forward, should_execute_overdue
from backend.scheduler.parsing import (
    ScheduleParseError,
    compute_next_run,
    parse_schedule,
    schedule_period_seconds,
)


class FakeDB:
    """Minimal DatabaseManager stand-in with scheduled_tasks table."""

    def __init__(self) -> None:
        """Initialize with scheduled_tasks table."""
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute(
            "CREATE TABLE scheduled_tasks ("
            "id TEXT PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "task_type TEXT NOT NULL CHECK (task_type IN ('reminder', 'agent_task')), "
            "prompt TEXT NOT NULL, "
            "schedule_kind TEXT NOT NULL "
            "CHECK (schedule_kind IN ('once', 'interval', 'cron')), "
            "schedule_expr TEXT NOT NULL, "
            "next_run_at TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'active' "
            "  CHECK (status IN ('active', 'paused', 'completed')), "
            "last_run_at TEXT, "
            "last_status TEXT, "
            "last_error TEXT, "
            "repeat_limit INTEGER, "
            "repeat_count INTEGER NOT NULL DEFAULT 0, "
            "allowed_tools TEXT NOT NULL DEFAULT '[]', "
            "conversation_id TEXT, "
            "deliver TEXT NOT NULL DEFAULT 'origin', "
            "timezone TEXT NOT NULL DEFAULT 'UTC', "
            "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))"
            ")"
        )
        self.connection.execute(
            "CREATE TABLE scheduled_task_runs ("
            "id TEXT PRIMARY KEY, "
            "task_id TEXT NOT NULL, "
            "started_at TEXT NOT NULL, "
            "finished_at TEXT, "
            "status TEXT NOT NULL CHECK "
            "  (status IN ('ok', 'error', 'timeout', 'silent', 'skipped')), "
            "result_text TEXT NOT NULL DEFAULT '', "
            "error_message TEXT NOT NULL DEFAULT '', "
            "duration_ms INTEGER NOT NULL DEFAULT 0"
            ")"
        )

    def execute(self, sql, params=()):
        """Execute a SQL statement."""
        return self.connection.execute(sql, params)

    def commit(self):
        """Commit the current transaction."""
        self.connection.commit()


@pytest.fixture
def fake_db():
    """Create a FakeDB instance for testing."""
    return FakeDB()


class TestScheduleParsing:
    """Tests for schedule expression parsing."""

    def test_parse_once_iso_timestamp(self):
        """ISO timestamp parses as one-shot schedule."""
        result = parse_schedule("2026-04-10T09:00:00")
        assert result["kind"] == "once"
        assert "2026-04-10" in result["run_at"]

    def test_parse_interval_minutes(self):
        """Minute interval parses correctly."""
        assert parse_schedule("30m") == {"kind": "interval", "minutes": 30}

    def test_parse_interval_hours(self):
        """Hour interval parses correctly."""
        assert parse_schedule("2h") == {"kind": "interval", "minutes": 120}

    def test_parse_interval_days(self):
        """Day interval parses correctly."""
        assert parse_schedule("1d") == {"kind": "interval", "minutes": 1440}

    def test_parse_cron_expression(self):
        """Cron expression parses correctly."""
        result = parse_schedule("0 9 * * *")
        assert result == {"kind": "cron", "cron_expr": "0 9 * * *"}

    def test_parse_invalid_raises(self):
        """Invalid expression raises ScheduleParseError."""
        with pytest.raises(ScheduleParseError):
            parse_schedule("not_a_schedule")

    def test_compute_next_run_once(self):
        """One-shot next run returns the scheduled time."""
        schedule = {"kind": "once", "run_at": "2026-04-10T09:00:00+00:00"}
        assert "2026-04-10" in compute_next_run(schedule)

    def test_compute_next_run_interval(self):
        """Interval next run is now + interval."""
        now = datetime(2026, 4, 6, 10, 0, 0, tzinfo=UTC)
        result = compute_next_run({"kind": "interval", "minutes": 60}, now=now)
        assert "2026-04-06T11:00:00" in result

    def test_compute_next_run_cron(self):
        """Cron next run is the next matching time."""
        now = datetime(2026, 4, 6, 8, 0, 0, tzinfo=UTC)
        result = compute_next_run({"kind": "cron", "cron_expr": "0 9 * * *"}, now=now)
        assert "09:00:00" in result

    def test_compute_next_run_cron_with_timezone(self):
        """Cron 9 AM in Moscow timezone resolves to 06:00 UTC."""
        now = datetime(2026, 4, 6, 0, 0, 0, tzinfo=UTC)
        result = compute_next_run(
            {"kind": "cron", "cron_expr": "0 9 * * *"},
            now=now,
            timezone="Europe/Moscow",
        )
        assert "06:00:00" in result

    def test_compute_next_run_invalid_timezone_falls_back(self):
        """Invalid timezone falls back to UTC."""
        now = datetime(2026, 4, 6, 8, 0, 0, tzinfo=UTC)
        result = compute_next_run(
            {"kind": "cron", "cron_expr": "0 9 * * *"},
            now=now,
            timezone="Invalid/Zone",
        )
        assert "09:00:00" in result

    def test_schedule_period_interval(self):
        """Interval period returns seconds."""
        assert schedule_period_seconds("interval", "30m") == 1800.0

    def test_schedule_period_once_is_none(self):
        """One-shot period is None."""
        assert schedule_period_seconds("once", "2026-04-10T09:00") is None


class TestScheduledTaskDBHandlers:
    """Tests for scheduled task DB proxy handlers."""

    @pytest.mark.asyncio
    async def test_create_and_list(self, fake_db):
        """Create a task and list it back."""
        create = make_create_scheduled_task_handler(fake_db)
        list_h = make_list_scheduled_tasks_handler(fake_db)
        result = await create(
            {
                "name": "Test Task",
                "task_type": "reminder",
                "prompt": "Hello",
                "schedule_kind": "once",
                "schedule_expr": "2026-04-10T09:00:00",
                "next_run_at": "2026-04-10T09:00:00",
            }
        )
        assert "task_id" in result
        tasks = await list_h({"status": "all"})
        assert len(tasks["tasks"]) == 1
        assert tasks["tasks"][0]["name"] == "Test Task"

    @pytest.mark.asyncio
    async def test_get_due_tasks(self, fake_db):
        """Get due tasks returns only overdue active tasks."""
        create = make_create_scheduled_task_handler(fake_db)
        get_due = make_get_due_tasks_handler(fake_db)
        await create(
            {
                "name": "Past",
                "task_type": "reminder",
                "prompt": "Due",
                "schedule_kind": "once",
                "schedule_expr": "2026-04-01T09:00:00",
                "next_run_at": "2026-04-01T09:00:00",
            }
        )
        await create(
            {
                "name": "Future",
                "task_type": "reminder",
                "prompt": "Not due",
                "schedule_kind": "once",
                "schedule_expr": "2099-01-01T00:00:00",
                "next_run_at": "2099-01-01T00:00:00",
            }
        )
        due = await get_due({"now": "2026-04-06T12:00:00"})
        assert len(due["tasks"]) == 1
        assert due["tasks"][0]["name"] == "Past"

    @pytest.mark.asyncio
    async def test_advance_next_run(self, fake_db):
        """Advance updates next_run_at and repeat_count."""
        create = make_create_scheduled_task_handler(fake_db)
        advance = make_advance_task_next_run_handler(fake_db)
        result = await create(
            {
                "name": "Recurring",
                "task_type": "agent_task",
                "prompt": "Check",
                "schedule_kind": "interval",
                "schedule_expr": "30m",
                "next_run_at": "2026-04-06T10:00:00",
            }
        )
        await advance(
            {
                "task_id": result["task_id"],
                "next_run_at": "2026-04-06T10:30:00",
                "repeat_count": 1,
            }
        )
        row = fake_db.execute(
            "SELECT next_run_at, repeat_count FROM scheduled_tasks WHERE id = ?",
            (result["task_id"],),
        ).fetchone()
        assert row[0] == "2026-04-06T10:30:00"
        assert row[1] == 1

    @pytest.mark.asyncio
    async def test_mark_task_run(self, fake_db):
        """Mark run updates last_run_at and last_status."""
        create = make_create_scheduled_task_handler(fake_db)
        mark = make_mark_task_run_handler(fake_db)
        result = await create(
            {
                "name": "Task",
                "task_type": "reminder",
                "prompt": "X",
                "schedule_kind": "once",
                "schedule_expr": "2026-04-10T09:00:00",
                "next_run_at": "2026-04-10T09:00:00",
            }
        )
        await mark(
            {
                "task_id": result["task_id"],
                "last_run_at": "2026-04-10T09:00:01",
                "last_status": "ok",
                "status": "completed",
            }
        )
        row = fake_db.execute(
            "SELECT status, last_status FROM scheduled_tasks WHERE id = ?",
            (result["task_id"],),
        ).fetchone()
        assert row[0] == "completed"
        assert row[1] == "ok"

    @pytest.mark.asyncio
    async def test_delete_task(self, fake_db):
        """Delete removes the task."""
        create = make_create_scheduled_task_handler(fake_db)
        delete = make_delete_scheduled_task_handler(fake_db)
        list_h = make_list_scheduled_tasks_handler(fake_db)
        result = await create(
            {
                "name": "To Delete",
                "task_type": "reminder",
                "prompt": "X",
                "schedule_kind": "once",
                "schedule_expr": "2026-04-10T09:00:00",
                "next_run_at": "2026-04-10T09:00:00",
            }
        )
        await delete({"task_id": result["task_id"]})
        tasks = await list_h({"status": "all"})
        assert len(tasks["tasks"]) == 0


class TestTaskRunHistory:
    """Tests for record_task_run and list_task_runs DB handlers."""

    @pytest.mark.asyncio
    async def test_record_and_list_runs(self, fake_db):
        """Records are inserted and listed newest-first."""
        record = make_record_task_run_handler(fake_db)
        list_runs = make_list_task_runs_handler(fake_db)

        await record(
            {
                "task_id": "task-1",
                "started_at": "2026-04-06T10:00:00",
                "finished_at": "2026-04-06T10:00:05",
                "status": "ok",
                "result_text": "Done",
                "error_message": "",
                "duration_ms": 5000,
            }
        )
        await record(
            {
                "task_id": "task-1",
                "started_at": "2026-04-06T11:00:00",
                "finished_at": "2026-04-06T11:00:02",
                "status": "error",
                "result_text": "",
                "error_message": "Network error",
                "duration_ms": 2000,
            }
        )

        result = await list_runs({"task_id": "task-1", "limit": 20})
        assert len(result["runs"]) == 2
        # Newest first
        assert result["runs"][0]["status"] == "error"
        assert result["runs"][0]["error_message"] == "Network error"
        assert result["runs"][1]["status"] == "ok"
        assert result["runs"][1]["duration_ms"] == 5000

    @pytest.mark.asyncio
    async def test_runs_filtered_by_task_id(self, fake_db):
        """list_task_runs returns only runs for the requested task."""
        record = make_record_task_run_handler(fake_db)
        list_runs = make_list_task_runs_handler(fake_db)

        await record(
            {
                "task_id": "task-A",
                "started_at": "2026-04-06T10:00:00",
                "finished_at": "2026-04-06T10:00:01",
                "status": "ok",
            }
        )
        await record(
            {
                "task_id": "task-B",
                "started_at": "2026-04-06T10:00:00",
                "finished_at": "2026-04-06T10:00:01",
                "status": "ok",
            }
        )

        result_a = await list_runs({"task_id": "task-A", "limit": 10})
        result_b = await list_runs({"task_id": "task-B", "limit": 10})
        assert len(result_a["runs"]) == 1
        assert len(result_b["runs"]) == 1
        assert result_a["runs"][0]["task_id"] == "task-A"
        assert result_b["runs"][0]["task_id"] == "task-B"

    @pytest.mark.asyncio
    async def test_runs_pruned_beyond_limit(self, fake_db):
        """Old run records are pruned to TASK_RUN_HISTORY_LIMIT per task."""
        from backend.processes.db_proxy.handlers.scheduler import (
            TASK_RUN_HISTORY_LIMIT,
        )

        record = make_record_task_run_handler(fake_db)

        # Insert one more than the limit
        for i in range(TASK_RUN_HISTORY_LIMIT + 5):
            await record(
                {
                    "task_id": "noisy",
                    "started_at": f"2026-04-06T10:{i:02d}:00",
                    "finished_at": f"2026-04-06T10:{i:02d}:01",
                    "status": "ok",
                }
            )

        count = fake_db.execute(
            "SELECT COUNT(*) FROM scheduled_task_runs WHERE task_id = ?",
            ("noisy",),
        ).fetchone()[0]
        assert count == TASK_RUN_HISTORY_LIMIT


class TestMissedTaskProtection:
    """Tests for overdue task detection and fast-forwarding."""

    def test_one_shot_always_executes(self):
        """One-shot overdue task should always execute."""
        task = {
            "schedule_kind": "once",
            "schedule_expr": "2026-04-01T09:00:00",
            "next_run_at": "2026-04-01T09:00:00",
        }
        assert (
            should_execute_overdue(task, datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC))
            is True
        )

    def test_interval_overdue_within_period(self):
        """Interval task overdue by less than period should execute."""
        task = {
            "schedule_kind": "interval",
            "schedule_expr": "2h",
            "next_run_at": "2026-04-06T10:00:00+00:00",
        }
        assert (
            should_execute_overdue(task, datetime(2026, 4, 6, 10, 30, 0, tzinfo=UTC))
            is True
        )

    def test_interval_overdue_beyond_period(self):
        """Interval task overdue by more than period should skip."""
        task = {
            "schedule_kind": "interval",
            "schedule_expr": "30m",
            "next_run_at": "2026-04-06T10:00:00+00:00",
        }
        assert (
            should_execute_overdue(task, datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC))
            is False
        )

    def test_fast_forward_computes_future_time(self):
        """Fast-forward returns the next future run time."""
        task = {
            "schedule_kind": "interval",
            "schedule_expr": "1h",
            "next_run_at": "2026-04-06T08:00:00+00:00",
        }
        result = compute_fast_forward(task, datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC))
        assert result is not None
        assert "2026-04-06T13:00:00" in result


class TestSchedulerLoop:
    """Tests for scheduler loop tick logic."""

    @pytest.mark.asyncio
    async def test_tick_finds_due_tasks(self):
        """Tick calls get_due_tasks."""
        from backend.scheduler.loop import _tick

        mock_db = AsyncMock()
        mock_db.get_due_tasks.return_value = []
        services = MagicMock()
        services.db_client = mock_db
        await _tick(services)
        mock_db.get_due_tasks.assert_called_once()

    @pytest.mark.asyncio
    async def test_pre_run_advance_called(self):
        """Recurring task has next_run_at advanced before execution."""
        from backend.scheduler.loop import _process_due_task

        mock_db = AsyncMock()
        mock_db.advance_task_next_run.return_value = {"ok": True}
        mock_db.mark_task_run.return_value = {"ok": True}
        services = MagicMock()
        services.db_client = mock_db
        services.sse_publish_fn = None

        with patch("backend.scheduler.loop.execute_task", return_value=("Done", True)):
            task = {
                "id": "t-123",
                "name": "Test",
                "task_type": "reminder",
                "prompt": "Hi",
                "schedule_kind": "interval",
                "schedule_expr": "30m",
                "next_run_at": datetime.now(tz=UTC).isoformat(),
                "repeat_count": 0,
                "repeat_limit": None,
            }
            await _process_due_task(services, task, datetime.now(tz=UTC))
        mock_db.advance_task_next_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_repeat_limit_marks_completed(self):
        """Task at repeat limit is marked completed."""
        from backend.scheduler.loop import _process_due_task

        mock_db = AsyncMock()
        mock_db.advance_task_next_run.return_value = {"ok": True}
        mock_db.mark_task_run.return_value = {"ok": True}
        services = MagicMock()
        services.db_client = mock_db

        task = {
            "id": "t-456",
            "name": "Limited",
            "task_type": "reminder",
            "prompt": "X",
            "schedule_kind": "interval",
            "schedule_expr": "1h",
            "next_run_at": datetime.now(tz=UTC).isoformat(),
            "repeat_count": 4,
            "repeat_limit": 5,
        }
        await _process_due_task(services, task, datetime.now(tz=UTC))
        mark_call = mock_db.mark_task_run.call_args[0][0]
        assert mark_call["status"] == "completed"


class TestToolAllowlist:
    """Tests for tool allowlist parsing."""

    def test_parse_valid_allowlist(self):
        """Valid JSON list is parsed."""
        from backend.scheduler.executor import _parse_allowed_tools

        assert _parse_allowed_tools('["web_search", "memory_recall"]') == [
            "web_search",
            "memory_recall",
        ]

    def test_parse_empty_allowlist(self):
        """Empty list returns empty."""
        from backend.scheduler.executor import _parse_allowed_tools

        assert _parse_allowed_tools("[]") == []

    def test_parse_invalid_json(self):
        """Invalid JSON returns empty."""
        from backend.scheduler.executor import _parse_allowed_tools

        assert _parse_allowed_tools("not json") == []

    def test_parse_non_array(self):
        """Non-array JSON returns empty."""
        from backend.scheduler.executor import _parse_allowed_tools

        assert _parse_allowed_tools('{"a": 1}') == []


class TestScheduleTaskTool:
    """Tests for the schedule_task LLM tool handler."""

    @pytest.mark.asyncio
    async def test_create_task(self):
        """Create action creates a task."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        mock_db = AsyncMock()
        mock_db.get_user_profile.return_value = {"profile": {"timezone": "UTC"}}
        mock_db.create_scheduled_task.return_value = {
            "task_id": "abc-123",
            "next_run_at": "2026-04-06T11:00:00",
        }
        handler = make_schedule_task_handler(mock_db)
        result = await handler(
            {
                "action": "create",
                "name": "Test",
                "prompt": "Check weather",
                "schedule": "1h",
                "task_type": "agent_task",
                "allowed_tools": '["web_search"]',
            }
        )
        assert "abc-123" in result

    @pytest.mark.asyncio
    async def test_create_invalid_schedule(self):
        """Create with invalid schedule returns error."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        handler = make_schedule_task_handler(AsyncMock())
        result = await handler(
            {"action": "create", "name": "Test", "prompt": "X", "schedule": "bad"}
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        """List with no tasks returns message."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        mock_db = AsyncMock()
        mock_db.list_scheduled_tasks.return_value = []
        handler = make_schedule_task_handler(mock_db)
        result = await handler({"action": "list"})
        assert "No scheduled tasks" in result

    @pytest.mark.asyncio
    async def test_pause_task(self):
        """Pause updates status."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        mock_db = AsyncMock()
        mock_db.update_scheduled_task.return_value = {"ok": True}
        handler = make_schedule_task_handler(mock_db)
        result = await handler({"action": "pause", "task_id": "abc-123"})
        assert "paused" in result

    @pytest.mark.asyncio
    async def test_remove_task(self):
        """Remove deletes the task."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        mock_db = AsyncMock()
        mock_db.delete_scheduled_task.return_value = {"ok": True}
        handler = make_schedule_task_handler(mock_db)
        result = await handler({"action": "remove", "task_id": "abc-123"})
        assert "removed" in result

    @pytest.mark.asyncio
    async def test_create_missing_name(self):
        """Create without name returns error."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        handler = make_schedule_task_handler(AsyncMock())
        result = await handler({"action": "create", "prompt": "X", "schedule": "1h"})
        assert "name is required" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        """Unknown action returns error."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        handler = make_schedule_task_handler(AsyncMock())
        result = await handler({"action": "unknown"})
        assert "Unknown action" in result

    @pytest.mark.asyncio
    async def test_broker_executes_schedule_task_with_validated_model(self):
        """Broker should pass validated models to schedule_task without crashing."""
        from backend.tools.broker import ToolBroker
        from backend.tools.builtin import ScheduleTaskInput
        from backend.tools.handlers.schedule import make_schedule_task_handler
        from backend.tools.registry import ToolDefinition, ToolRegistry, WorkerTarget
        from backend.trust.zones import TrustZone

        mock_db = AsyncMock()
        mock_db.get_user_profile.return_value = {"profile": {"timezone": "UTC"}}
        mock_db.create_scheduled_task.return_value = {
            "task_id": "broker-123",
            "next_run_at": "2026-04-06T11:00:00",
        }

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="schedule_task",
                description="Manage scheduled tasks",
                input_model=ScheduleTaskInput,
                zone=TrustZone.YELLOW,
                worker=WorkerTarget.CORE,
            )
        )
        broker = ToolBroker(registry=registry)
        broker.set_handler("schedule_task", make_schedule_task_handler(mock_db))

        result = await broker.execute(
            "schedule_task",
            {
                "action": "create",
                "name": "Daily AI digest",
                "prompt": "Collect AI news",
                "schedule": "0 7 * * *",
                "task_type": "agent_task",
                "allowed_tools": '["web_search"]',
            },
        )

        assert result.success
        assert "broker-123" in str(result.result)


class TestAllowlistEnforcement:
    """Tests for tool allowlist enforcement in PromptBuilder and ToolExecutor."""

    def test_prompt_builder_filters_to_allowlist(self):
        """PromptBuilder.select_openai_tools respects allowed_tool_names."""
        from backend.agent.prompt_builder import PromptBuilder

        mock_broker = MagicMock()
        mock_registry = MagicMock()
        mock_registry.tool_names = ["web_search", "file_write", "memory_recall"]
        mock_broker.registry = mock_registry
        mock_broker.list_openai_schemas.return_value = [
            {"function": {"name": "web_search"}},
        ]

        builder = PromptBuilder("test-model", {}, mock_broker, None)
        builder.allowed_tool_names = {"web_search", "memory_recall"}
        builder.select_openai_tools("any message")

        call_kwargs = mock_broker.list_openai_schemas.call_args
        allowed_names = call_kwargs.kwargs["allowed_names"]
        assert "web_search" in allowed_names
        assert "memory_recall" in allowed_names
        assert "file_write" not in allowed_names

    def test_prompt_builder_no_allowlist_no_restriction(self):
        """When allowed_tool_names is None, normal selection applies."""
        from backend.agent.prompt_builder import PromptBuilder

        mock_broker = MagicMock()
        mock_registry = MagicMock()
        mock_registry.tool_names = ["web_search", "memory_recall"]
        mock_broker.registry = mock_registry
        mock_broker.list_openai_schemas.return_value = []

        builder = PromptBuilder("test-model", {}, mock_broker, None)
        builder.allowed_tool_names = None
        builder.select_openai_tools("any message")

        call_kwargs = mock_broker.list_openai_schemas.call_args
        allowed_names = call_kwargs.kwargs["allowed_names"]
        assert "web_search" in allowed_names
        assert "memory_recall" in allowed_names

    def test_prompt_builder_hides_file_read_for_pasted_code(self):
        """Pasted code should be analyzed from prompt text, not file tools."""
        from backend.agent.prompt_builder import PromptBuilder

        mock_broker = MagicMock()
        mock_registry = MagicMock()
        mock_registry.tool_names = [
            "web_search",
            "file_read",
            "file_search",
            "memory_recall",
        ]
        mock_broker.registry = mock_registry
        mock_broker.list_openai_schemas.return_value = []

        builder = PromptBuilder("test-model", {}, mock_broker, None)
        builder.select_openai_tools(
            "Проанализируй этот код:\n```python\ndef f():\n    return 1\n```"
        )

        allowed_names = mock_broker.list_openai_schemas.call_args.kwargs[
            "allowed_names"
        ]
        assert "file_read" not in allowed_names
        assert "file_search" not in allowed_names
        assert "memory_recall" in allowed_names

    def test_prompt_builder_allows_file_read_for_explicit_file_path(self):
        """Explicit file/path requests may use file tools."""
        from backend.agent.prompt_builder import PromptBuilder

        mock_broker = MagicMock()
        mock_registry = MagicMock()
        mock_registry.tool_names = ["file_read", "file_search", "memory_recall"]
        mock_broker.registry = mock_registry
        mock_broker.list_openai_schemas.return_value = []

        builder = PromptBuilder("test-model", {}, mock_broker, None)
        builder.select_openai_tools("Прочитай файл sandbox/CODE_RULES.md")

        allowed_names = mock_broker.list_openai_schemas.call_args.kwargs[
            "allowed_names"
        ]
        assert "file_read" in allowed_names
        assert "file_search" in allowed_names

    @pytest.mark.asyncio
    async def test_tool_executor_rejects_disallowed_tool(self):
        """ToolExecutor raises PermissionError for tools not in allowlist."""
        from backend.agent.tool_executor import ToolExecutor
        from backend.trust.confirmation import ConfirmationManager

        mock_broker = MagicMock()
        mock_broker.execute = AsyncMock(return_value=MagicMock())
        executor = ToolExecutor(mock_broker, ConfirmationManager())
        executor.allowed_tool_names = {"web_search"}

        tool_call = MagicMock()
        tool_call.name = "file_write"
        tool_call.parameters = {}

        with pytest.raises(PermissionError, match="not in the allowlist"):
            await executor._execute_with_status(tool_call)

    @pytest.mark.asyncio
    async def test_tool_executor_allows_permitted_tool(self):
        """ToolExecutor runs tools that are in the allowlist."""
        from backend.agent.tool_executor import ToolExecutor
        from backend.trust.confirmation import ConfirmationManager

        mock_broker = MagicMock()
        mock_result = MagicMock()
        mock_broker.execute = AsyncMock(return_value=mock_result)
        executor = ToolExecutor(mock_broker, ConfirmationManager())
        executor.allowed_tool_names = {"web_search"}

        tool_call = MagicMock()
        tool_call.name = "web_search"
        tool_call.parameters = {"q": "test"}

        result = await executor._execute_with_status(tool_call)
        assert result is mock_result


class TestToolBrokerContextPropagation:
    """Tests for broker context propagation to schedule_task handler."""

    @pytest.mark.asyncio
    async def test_conversation_id_inherited_from_broker_context(self):
        """When LLM omits conversation_id, broker context is used."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        mock_db = AsyncMock()
        mock_db.get_user_profile.return_value = {"profile": {"timezone": "UTC"}}
        mock_db.create_scheduled_task.return_value = {
            "task_id": "ctx-123",
            "next_run_at": "2026-04-06T11:00:00",
        }
        broker = MagicMock()
        broker.current_context = {"conversation_id": "conv-from-context"}

        handler = make_schedule_task_handler(mock_db, broker=broker)
        await handler(
            {
                "action": "create",
                "name": "Test",
                "prompt": "X",
                "schedule": "1h",
                "task_type": "agent_task",
                "allowed_tools": "[]",
            }
        )
        call_args = mock_db.create_scheduled_task.call_args[0][0]
        assert call_args["conversation_id"] == "conv-from-context"

    @pytest.mark.asyncio
    async def test_explicit_conversation_id_wins_over_context(self):
        """Explicit conversation_id from LLM is not overwritten."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        mock_db = AsyncMock()
        mock_db.get_user_profile.return_value = {"profile": {"timezone": "UTC"}}
        mock_db.create_scheduled_task.return_value = {
            "task_id": "x",
            "next_run_at": "2026-04-06T11:00:00",
        }
        broker = MagicMock()
        broker.current_context = {"conversation_id": "from-context"}

        handler = make_schedule_task_handler(mock_db, broker=broker)
        await handler(
            {
                "action": "create",
                "name": "T",
                "prompt": "X",
                "schedule": "1h",
                "task_type": "agent_task",
                "allowed_tools": "[]",
                "conversation_id": "explicit-id",
            }
        )
        call_args = mock_db.create_scheduled_task.call_args[0][0]
        assert call_args["conversation_id"] == "explicit-id"

    @pytest.mark.asyncio
    async def test_no_broker_falls_back_to_empty(self):
        """Without broker, conversation_id stays empty if not provided."""
        from backend.tools.handlers.schedule import make_schedule_task_handler

        mock_db = AsyncMock()
        mock_db.get_user_profile.return_value = {"profile": {"timezone": "UTC"}}
        mock_db.create_scheduled_task.return_value = {
            "task_id": "x",
            "next_run_at": "2026-04-06T11:00:00",
        }
        handler = make_schedule_task_handler(mock_db, broker=None)
        await handler(
            {
                "action": "create",
                "name": "T",
                "prompt": "X",
                "schedule": "1h",
                "task_type": "agent_task",
                "allowed_tools": "[]",
            }
        )
        call_args = mock_db.create_scheduled_task.call_args[0][0]
        assert call_args["conversation_id"] == ""


class TestBridgeDelivery:
    """Tests for chat bridge delivery of scheduled task results."""

    @pytest.mark.asyncio
    async def test_bridge_skipped_when_service_missing(self):
        """No bridge_service in services means delivery is skipped silently."""
        from backend.scheduler.executor import _deliver_to_bridge

        services = MagicMock()
        services.bridge_service = None
        task = {
            "id": "t-1",
            "name": "Test",
            "task_type": "reminder",
            "conversation_id": "conv-123",
        }
        await _deliver_to_bridge(services, task, "result")
        # No exception expected, no calls made

    @pytest.mark.asyncio
    async def test_bridge_skipped_without_conversation_id(self):
        """No conversation_id in task means bridge call is skipped."""
        from backend.scheduler.executor import _deliver_to_bridge

        bridge = AsyncMock()
        services = MagicMock()
        services.bridge_service = bridge
        task = {
            "id": "t-1",
            "name": "Test",
            "task_type": "reminder",
            "conversation_id": "",
        }
        await _deliver_to_bridge(services, task, "result")
        bridge.sync_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_bridge_defaults_to_task_conversation(self):
        """Without a target, delivery uses the task conversation, result bare."""
        from backend.scheduler.executor import _deliver_to_bridge

        bridge = AsyncMock()
        services = MagicMock()
        services.bridge_service = bridge
        task = {
            "id": "t-99",
            "name": "Daily check",
            "task_type": "agent_task",
            "conversation_id": "conv-abc",
        }
        await _deliver_to_bridge(services, task, "All systems healthy")
        bridge.sync_outbound_message.assert_called_once()
        call_kwargs = bridge.sync_outbound_message.call_args.kwargs
        assert call_kwargs["conversation_id"] == "conv-abc"
        assert call_kwargs["role"] == "assistant"
        # Agent-task result goes out bare — no "[Scheduled task] Daily check".
        assert call_kwargs["content"] == "All systems healthy"

    @pytest.mark.asyncio
    async def test_bridge_explicit_conversation_and_message_id(self):
        """An explicit conversation_id/message_id override the task defaults."""
        from backend.scheduler.executor import _deliver_to_bridge

        bridge = AsyncMock()
        services = MagicMock()
        services.bridge_service = bridge
        task = {
            "id": "t-99",
            "name": "Daily check",
            "task_type": "agent_task",
            "conversation_id": "conv-abc",
        }
        await _deliver_to_bridge(
            services,
            task,
            "Price is $100",
            conversation_id="sched_t-99",
            message_id="real-msg-id",
        )
        call_kwargs = bridge.sync_outbound_message.call_args.kwargs
        assert call_kwargs["conversation_id"] == "sched_t-99"
        assert call_kwargs["message_id"] == "real-msg-id"
        assert call_kwargs["content"] == "Price is $100"

    @pytest.mark.asyncio
    async def test_bridge_failure_does_not_raise(self):
        """A bridge delivery failure is logged but does not raise."""
        from backend.scheduler.executor import _deliver_to_bridge

        bridge = AsyncMock()
        bridge.sync_outbound_message.side_effect = RuntimeError("offline")
        services = MagicMock()
        services.bridge_service = bridge
        task = {
            "id": "t-1",
            "name": "Test",
            "task_type": "reminder",
            "conversation_id": "conv-1",
        }
        # Should not raise
        await _deliver_to_bridge(services, task, "result")
        bridge.sync_outbound_message.assert_called_once()


class TestScheduledTaskSystemPrompt:
    """Tests for the scheduled-task system prompt."""

    def test_default_mode_uses_normal_prompt(self):
        """Without scheduled_task_mode the normal prompt is built."""
        from backend.agent.prompt_builder import PromptBuilder

        builder = PromptBuilder("test-model", {}, MagicMock(), None)
        prompt = builder.build_system_prompt()
        assert "```suggestions" in prompt
        assert "[SILENT]" not in prompt

    def test_scheduled_mode_omits_suggestions_when_disabled(self):
        """A scheduled task with suggestions disabled omits the block."""
        from backend.agent.prompt_builder import PromptBuilder

        builder = PromptBuilder("test-model", {}, MagicMock(), None)
        builder.scheduled_task_mode = True
        builder.suggestions_enabled = False
        prompt = builder.build_system_prompt()
        assert "```suggestions" not in prompt
        assert "scheduled background task" in prompt

    def test_scheduled_mode_includes_suggestions_when_enabled(self):
        """A scheduled task with suggestions enabled includes the block."""
        from backend.agent.prompt_builder import PromptBuilder

        builder = PromptBuilder("test-model", {}, MagicMock(), None)
        builder.scheduled_task_mode = True
        builder.suggestions_enabled = True
        prompt = builder.build_system_prompt()
        assert "```suggestions" in prompt
        assert "scheduled background task" in prompt

    def test_normal_mode_omits_suggestions_when_disabled(self):
        """An interactive chat with suggestions disabled omits the block."""
        from backend.agent.prompt_builder import PromptBuilder

        builder = PromptBuilder("test-model", {}, MagicMock(), None)
        builder.suggestions_enabled = False
        prompt = builder.build_system_prompt()
        assert "```suggestions" not in prompt

    def test_scheduled_mode_includes_silent_marker(self):
        """In scheduled_task_mode the [SILENT] marker is documented."""
        from backend.agent.prompt_builder import PromptBuilder

        builder = PromptBuilder("test-model", {}, MagicMock(), None)
        builder.scheduled_task_mode = True
        prompt = builder.build_system_prompt()
        assert "[SILENT]" in prompt

    def test_scheduled_mode_preserves_datetime(self):
        """In scheduled_task_mode the current datetime is still injected."""
        from backend.agent.prompt_builder import PromptBuilder

        builder = PromptBuilder("test-model", {}, MagicMock(), None)
        builder.scheduled_task_mode = True
        prompt = builder.build_system_prompt()
        assert "Current datetime:" in prompt


class TestDialogRecorderIsolation:
    """Tests for dialog recorder isolation in scheduler sessions."""

    @pytest.mark.asyncio
    async def test_scheduler_session_skipped(self):
        """Sessions with sched_ prefix do not write to dialog recorder."""
        from backend.agent.message_persistence import MessagePersistence

        save_record = AsyncMock()
        persistence = MessagePersistence(save_fn=None, save_record_fn=save_record)

        await persistence.persist_dialog_record(
            conversation_id="sched_abc_20260406",
            session_id="sched_abc",
            record_input="test",
            source_message_ids=[],
        )
        save_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_session_still_writes(self):
        """Non-scheduler sessions still trigger save_record_fn."""
        from backend.agent.message_persistence import MessagePersistence

        save_record = AsyncMock()
        persistence = MessagePersistence(save_fn=None, save_record_fn=save_record)

        await persistence.persist_dialog_record(
            conversation_id="abc-123",
            session_id="normal-session",
            record_input="test",
            source_message_ids=[],
        )
        save_record.assert_called_once()
