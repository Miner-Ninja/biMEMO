"""Tests for tool helper implementations.

Covers system status handlers and memory recall
with mock database and system interfaces.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from backend.tools.builtin import (
    MemoryRecallInput,
    SystemStatusInput,
)
from backend.tools.handlers.registration import (
    _make_memory_recall_handler,
)
from backend.tools.handlers.system_status import (
    _fmt_bytes,
    _fmt_duration,
    make_system_status_handler,
)


class TestSystemStatusHandler:
    """Tests for system_status handler."""

    @pytest.mark.asyncio
    async def test_collects_all_metrics(self):
        """Verify all status sections are present."""
        handler = make_system_status_handler(
            data_dir="/tmp",
            ready_since=time.time() - 3600,
            get_ollama_status=lambda: "connected",
            get_worker_count=lambda: 2,
        )
        params = SystemStatusInput()
        result = await handler(params)

        assert "Disk:" in result
        assert "RAM (process):" in result
        assert "RAM (system):" in result
        assert "Ollama: connected" in result
        assert "Workers: 2" in result
        assert "Uptime:" in result

    @pytest.mark.asyncio
    async def test_formats_uptime(self):
        """Verify uptime is formatted correctly."""
        handler = make_system_status_handler(
            data_dir="/tmp",
            ready_since=time.time() - 7200,
            get_ollama_status=lambda: "connected",
            get_worker_count=lambda: 1,
        )
        params = SystemStatusInput()
        result = await handler(params)
        assert "2h" in result

    @pytest.mark.asyncio
    async def test_disk_unavailable(self):
        """Verify graceful handling of disk error."""
        with patch("backend.tools.handlers.system_status.psutil") as mock_psutil:
            mock_psutil.disk_usage.side_effect = OSError("not found")
            mock_psutil.Process.return_value.memory_info.return_value = MagicMock(
                rss=100_000_000
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                used=4_000_000_000,
                total=8_000_000_000,
                percent=50.0,
            )
            handler = make_system_status_handler(
                data_dir="/nonexistent",
                ready_since=time.time() - 60,
                get_ollama_status=lambda: "unknown",
                get_worker_count=lambda: 0,
            )
            params = SystemStatusInput()
            result = await handler(params)
            assert "Disk: unavailable" in result

    @pytest.mark.asyncio
    async def test_ollama_status_passthrough(self):
        """Verify Ollama status is passed through."""
        handler = make_system_status_handler(
            data_dir="/tmp",
            ready_since=time.time(),
            get_ollama_status=lambda: "model loaded: llama3",
            get_worker_count=lambda: 2,
        )
        params = SystemStatusInput()
        result = await handler(params)
        assert "model loaded: llama3" in result

    @pytest.mark.asyncio
    async def test_db_size_reported(self, tmp_path):
        """Verify database file size is reported."""
        db_file = tmp_path / "test.db"
        db_file.write_bytes(b"\x00" * 1024)

        handler = make_system_status_handler(
            data_dir="/tmp",
            ready_since=time.time(),
            get_ollama_status=lambda: "connected",
            get_worker_count=lambda: 1,
            db_path=str(db_file),
        )
        params = SystemStatusInput()
        result = await handler(params)
        assert "Database:" in result
        assert "WARNING" not in result

    @pytest.mark.asyncio
    async def test_db_size_warning_threshold(self, tmp_path):
        """Verify warning when DB exceeds 2GB threshold."""
        db_file = tmp_path / "large.db"
        db_file.write_bytes(b"\x00" * 10)

        handler = make_system_status_handler(
            data_dir="/tmp",
            ready_since=time.time(),
            get_ollama_status=lambda: "connected",
            get_worker_count=lambda: 1,
            db_path=str(db_file),
        )

        # Patch stat to return large file size
        large_stat = MagicMock()
        large_stat.st_size = 3 * 1024 * 1024 * 1024  # 3 GB
        with patch(
            "backend.tools.handlers.system_status.os.stat",
            return_value=large_stat,
        ):
            params = SystemStatusInput()
            result = await handler(params)
            assert "WARNING" in result
            assert "2GB" in result

    @pytest.mark.asyncio
    async def test_db_path_none_skips_db_size(self):
        """Verify no db_path skips database size section."""
        handler = make_system_status_handler(
            data_dir="/tmp",
            ready_since=time.time(),
            get_ollama_status=lambda: "connected",
            get_worker_count=lambda: 1,
        )
        params = SystemStatusInput()
        result = await handler(params)
        assert "Database:" not in result


class _FakeMemoryRecallDBClient:
    """Minimal DB client stub for memory recall handler tests."""

    def __init__(
        self,
        *,
        record_results: list[dict[str, object]] | None = None,
        message_results: list[dict[str, object]] | None = None,
        counts: dict[str, int] | None = None,
        summaries: list[dict[str, object]] | None = None,
        records: list[dict[str, object]] | None = None,
        messages: list[dict[str, object]] | None = None,
        summary_results: list[dict[str, object]] | None = None,
    ) -> None:
        """Store fixed responses and collect calls."""
        self.record_results = record_results or []
        self.message_results = message_results or []
        self.summary_results = summary_results or []
        self.counts = counts or {"messages": 0, "records": 0, "summaries": 0}
        self.summaries = summaries or []
        self.records = records or []
        self.messages = messages or []
        self.fulltext_calls: list[tuple[str, str, int]] = []
        self.count_calls = 0
        self.summary_calls: list[tuple[int, int]] = []
        self.record_calls: list[tuple[int, int]] = []
        self.message_calls: list[tuple[int, int, str | None]] = []

    async def fulltext_search(
        self,
        *,
        query: str,
        table: str,
        limit: int,
    ) -> list[dict[str, object]]:
        """Return fixed full-text search rows and record the request."""
        self.fulltext_calls.append((query, table, limit))
        if table == "records":
            return self.record_results
        if table == "messages":
            return self.message_results
        if table == "summaries":
            return self.summary_results
        return []

    async def count_memory_entries(self) -> dict[str, int]:
        """Return fixed memory counts."""
        self.count_calls += 1
        return self.counts

    async def list_summaries_full(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """Return fixed summary rows."""
        self.summary_calls.append((limit, offset))
        return self.summaries[offset : offset + limit]

    async def list_records(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """Return fixed record rows."""
        self.record_calls.append((limit, offset))
        return self.records[offset : offset + limit]

    async def list_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Return fixed message rows."""
        self.message_calls.append((limit, offset, conversation_id))
        return self.messages[offset : offset + limit]


class TestMemoryRecallHandler:
    """Tests for the memory recall tool handler."""

    @pytest.mark.asyncio
    async def test_general_query_uses_fast_recall_only(self) -> None:
        """General recall should stay on the current full-text path."""
        db_client = _FakeMemoryRecallDBClient(
            record_results=[
                {"summary": "User works with Docker.", "created_at": "2026-07-01"}
            ],
            message_results=[{"role": "user", "content": "We use Compose in prod."}],
            summary_results=[
                {"main_point": "User runs a containerized stack."}
            ],
        )
        handler = _make_memory_recall_handler(db_client)

        result = await handler(
            MemoryRecallInput(query="What do you remember about my stack?"),
        )

        assert "User runs a containerized stack." in result["result"]
        assert "[2026-07-01] User works with Docker." in result["result"]
        assert "[user] We use Compose in prod." in result["result"]
        assert db_client.count_calls == 0
        assert db_client.fulltext_calls == [
            ("What do you remember about my stack?", "summaries", 5),
            ("What do you remember about my stack?", "records", 5),
            ("What do you remember about my stack?", "messages", 5),
        ]

    @pytest.mark.asyncio
    async def test_source_question_returns_evidence_backed_output(self) -> None:
        """Source questions should include summary, records, and messages."""
        db_client = _FakeMemoryRecallDBClient(
            counts={"messages": 2, "records": 1, "summaries": 1},
            summaries=[
                {
                    "id": "summary-1",
                    "main_point": "We chose CSV for export.",
                    "based_on": ["Tableau compatibility"],
                    "record_relations": [
                        {"record_id": "record-1", "relation": "supports"}
                    ],
                }
            ],
            records=[
                {
                    "id": "record-1",
                    "summary": "CSV was chosen because Tableau supports it well.",
                    "source_message_ids": ["msg-1", "msg-2"],
                }
            ],
            messages=[
                {
                    "id": "msg-1",
                    "role": "user",
                    "content": "We need an export Tableau can read.",
                    "created_at": "2026-03-30T10:00:00Z",
                },
                {
                    "id": "msg-2",
                    "role": "assistant",
                    "content": "CSV fits Tableau and stays simple.",
                    "created_at": "2026-03-30T10:00:05Z",
                },
            ],
        )
        handler = _make_memory_recall_handler(db_client)

        result = await handler(MemoryRecallInput(query="Which messages support that?"))

        assert "Summary memory:" in result["result"]
        assert "We chose CSV for export." in result["result"]
        assert "Linked records:" in result["result"]
        assert "CSV was chosen because Tableau supports it well." in result["result"]
        assert "Source messages:" in result["result"]
        assert (
            "2026-03-30T10:00:00Z user: We need an export Tableau can read."
            in result["result"]
        )
        assert db_client.fulltext_calls == []
        assert db_client.count_calls == 1

    @pytest.mark.asyncio
    async def test_source_question_falls_back_to_fast_recall_when_bundle_missing(
        self,
    ) -> None:
        """Deep recall should fall back to the fast path when evidence is missing."""
        db_client = _FakeMemoryRecallDBClient(
            record_results=[{"summary": "We picked CSV for exports."}],
            counts={"messages": 0, "records": 0, "summaries": 0},
        )
        handler = _make_memory_recall_handler(db_client)

        result = await handler(MemoryRecallInput(query="Why did we choose CSV?"))

        assert result["result"] == "- We picked CSV for exports."
        assert db_client.count_calls == 1
        assert db_client.fulltext_calls == [
            ("Why did we choose CSV?", "summaries", 5),
            ("Why did we choose CSV?", "records", 5),
            ("Why did we choose CSV?", "messages", 5),
        ]


class TestFormatHelpers:
    """Tests for formatting helper functions."""

    def test_fmt_bytes_small(self):
        """Verify bytes formatting."""
        assert _fmt_bytes(512) == "512.0 B"

    def test_fmt_bytes_mb(self):
        """Verify MB formatting."""
        result = _fmt_bytes(1_500_000)
        assert "MB" in result

    def test_fmt_bytes_gb(self):
        """Verify GB formatting."""
        result = _fmt_bytes(2_000_000_000)
        assert "GB" in result

    def test_fmt_duration_seconds(self):
        """Verify seconds formatting."""
        assert _fmt_duration(45) == "45s"

    def test_fmt_duration_minutes(self):
        """Verify minutes formatting."""
        result = _fmt_duration(125)
        assert "2m" in result

    def test_fmt_duration_hours(self):
        """Verify hours formatting."""
        result = _fmt_duration(7230)
        assert "2h" in result
