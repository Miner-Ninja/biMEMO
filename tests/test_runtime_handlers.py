"""Tests for runtime DB proxy handlers without importing full db_proxy package."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest
from backend.security.runtime_schema import initialize_runtime_schema


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if not BACKEND_ROOT.exists():
    BACKEND_ROOT = ROOT / "app" / "backend"
DB_PROXY_ROOT = BACKEND_ROOT / "processes" / "db_proxy"


class RuntimeDB:
    """Small sqlite-backed stand-in for DatabaseManager."""

    def __init__(self) -> None:
        """Initialize an in-memory runtime database."""
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        initialize_runtime_schema(self)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute one SQL statement."""
        return self.connection.execute(sql, params)

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()


def _load_runtime_permissions_module() -> types.ModuleType:
    """Load the permissions handler module without importing db_proxy __init__."""
    package = types.ModuleType("backend.processes.db_proxy")
    package.__path__ = [str(DB_PROXY_ROOT)]
    sys.modules["backend.processes.db_proxy"] = package
    handlers = types.ModuleType("backend.processes.db_proxy.handlers")
    handlers.__path__ = [str(DB_PROXY_ROOT / "handlers")]
    sys.modules["backend.processes.db_proxy.handlers"] = handlers

    for name, path in (
        ("backend.processes.db_proxy.common", DB_PROXY_ROOT / "common.py"),
        ("backend.processes.db_proxy.params", DB_PROXY_ROOT / "params.py"),
        (
            "backend.processes.db_proxy.handlers.permissions",
            DB_PROXY_ROOT / "handlers" / "permissions.py",
        ),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec
        assert spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules["backend.processes.db_proxy.handlers.permissions"]


@pytest.mark.asyncio
async def test_pending_action_stores_resume_snapshot_separately() -> None:
    """Pending action preview keeps only a pointer to the runtime snapshot."""
    handlers = _load_runtime_permissions_module()
    db = RuntimeDB()
    create_run = handlers.make_create_agent_run_handler(db)
    create_action = handlers.make_create_pending_action_handler(db)
    get_snapshot = handlers.make_get_resume_snapshot_handler(db)

    run = await create_run(
        {
            "conversation_id": "conv-1",
            "session_id": "session-1",
            "channel": "web",
        }
    )
    action = await create_action(
        {
            "run_id": run["agent_run"]["id"],
            "action_id": "action-1",
            "tool_name": "email_send",
            "parameters": {"to": "a@example.com"},
            "channel": "web",
            "preview": {"tool_call_id": "call-1"},
            "resume_snapshot": {
                "tool_call_id": "call-1",
                "messages": [{"role": "user", "content": "send"}],
                "assistant_message": {"role": "assistant", "tool_calls": []},
            },
        }
    )

    preview = action["pending_action"]["preview"]
    assert preview["tool_call_id"] == "call-1"
    assert "messages" not in preview
    assert preview["resume_snapshot_id"]

    snapshot = await get_snapshot({"action_id": "action-1"})
    assert snapshot["resume_snapshot"]["id"] == preview["resume_snapshot_id"]
    assert snapshot["resume_snapshot"]["snapshot"]["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_prune_runtime_state_expires_actions_and_removes_snapshots() -> None:
    """Runtime pruning expires waiting actions and deletes stale snapshots."""
    handlers = _load_runtime_permissions_module()
    db = RuntimeDB()
    create_run = handlers.make_create_agent_run_handler(db)
    create_action = handlers.make_create_pending_action_handler(db)
    prune = handlers.make_prune_runtime_state_handler(db)

    run = await create_run({"conversation_id": "conv-1"})
    await create_action(
        {
            "run_id": run["agent_run"]["id"],
            "action_id": "action-1",
            "tool_name": "email_send",
            "channel": "web",
            "expires_at": "2026-05-09T10:00:00.000",
            "resume_snapshot": {"messages": [], "assistant_message": {}},
        }
    )

    result = await prune(
        {
            "now": "2026-05-09T12:00:00.000",
            "snapshot_retention_hours": 1,
            "run_retention_days": 1,
        }
    )

    action = db.execute(
        "SELECT status, decision FROM pending_actions WHERE id = ?",
        ("action-1",),
    ).fetchone()
    snapshot_count = db.execute("SELECT COUNT(*) FROM resume_snapshots").fetchone()
    assert result["expired_pending_actions"] == 1
    assert result["deleted_resume_snapshots"] == 1
    assert action == ("expired", "expired")
    assert snapshot_count == (0,)


def test_registry_routes_runtime_operations_to_runtime_db() -> None:
    """Registry source keeps durable policy rows in core and lifecycle in runtime."""
    registry_source = (DB_PROXY_ROOT / "registry.py").read_text()

    assert "make_create_capability_grant_handler(core_db)" in registry_source
    assert "make_list_project_roots_handler(core_db)" in registry_source
    assert "make_create_agent_run_handler(runtime_db)" in registry_source
    assert "make_create_pending_action_handler(runtime_db)" in registry_source
    assert "make_get_resume_snapshot_handler(runtime_db)" in registry_source
    assert "make_prune_runtime_state_handler(runtime_db)" in registry_source


def test_health_check_covers_core_runtime_and_diagnostics_dbs() -> None:
    """Health check should include every DB connection owned by DB proxy."""
    registry_source = (DB_PROXY_ROOT / "registry.py").read_text()

    assert "databases = [core_db]" in registry_source
    assert "databases.append(runtime_db)" in registry_source
    assert "databases.append(diagnostics_db)" in registry_source
    assert "return all(db.is_open for db in databases)" in registry_source


def test_approve_flow_fails_visibly_when_resume_snapshot_missing() -> None:
    """Approve path should fail before tool execution if snapshot is gone."""
    process_source = (
        BACKEND_ROOT / "processes" / "orchestrator" / "process.py"
    ).read_text()

    missing_check = process_source.index('if resume_context == "missing":')
    tool_execution = process_source.index("broker.set_context(context)")
    assert missing_check < tool_execution
    assert "status=\"failed\"" in process_source
    assert "resume snapshot is no longer available" in process_source


def test_startup_copies_legacy_core_runtime_rows_without_deleting() -> None:
    """DB proxy startup should copy legacy runtime rows into runtime DB."""
    targets_source = (BACKEND_ROOT / "processes" / "targets.py").read_text()

    assert "_migrate_core_runtime_state(core_db, runtime_db, logger)" in targets_source
    assert "INSERT OR IGNORE INTO agent_runs" in targets_source
    assert "INSERT OR IGNORE INTO pending_actions" in targets_source
    assert "DELETE FROM agent_runs" not in targets_source
    assert "DELETE FROM pending_actions" not in targets_source


def test_legacy_pending_preview_is_split_into_snapshot_reference() -> None:
    """Legacy preview payloads should become small previews plus snapshots."""
    targets_source = (BACKEND_ROOT / "processes" / "targets.py").read_text()

    assert 'preview.pop("messages", [])' in targets_source
    assert 'preview.pop("assistant_message", {})' in targets_source
    assert 'preview["resume_snapshot_id"] = snapshot_id' in targets_source
