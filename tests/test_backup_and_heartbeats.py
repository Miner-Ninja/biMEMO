"""Tests for scheduled-backup rotation and background-loop heartbeats.

Covers the extracted backup/rotation functions the scheduled loop uses
and the heartbeat operations feeding /admin/memory/health.
"""

import os
import secrets

import pytest

from backend.processes.db_proxy.handlers.admin import (
    make_get_loop_heartbeats_handler,
    make_record_loop_heartbeat_handler,
    perform_backup,
    prune_old_backups,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


def _open_db(path):
    """Create and open one schema-initialized database."""
    db = DatabaseManager(str(path))
    db.open(bytearray(secrets.token_bytes(32)))
    initialize_schema(db)
    return db


class TestBackupRotation:
    """perform_backup writes copies; prune_old_backups rotates them."""

    def test_backup_writes_copy_per_database(self, tmp_path):
        """Each open database gets one timestamped copy."""
        db = _open_db(tmp_path / "core.db")
        paths = perform_backup({"core": db}, str(tmp_path / "core.db"))
        assert set(paths) == {"core"}
        assert os.path.isfile(paths["core"])
        db.close()

    def test_rotation_keeps_newest_copies(self, tmp_path):
        """Old copies beyond the keep limit are deleted, newest stay."""
        db = _open_db(tmp_path / "core.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(mode=0o700)
        names = [f"backup_core_2026010{i}_000000.db" for i in range(1, 6)]
        for name in names:
            (backup_dir / name).write_bytes(b"x")
        deleted = prune_old_backups(
            {"core": db}, str(tmp_path / "core.db"), keep=2
        )
        assert deleted == 3
        remaining = sorted(os.listdir(backup_dir))
        assert remaining == names[-2:]
        db.close()

    def test_rotation_ignores_other_databases_files(self, tmp_path):
        """Rotation for one name never touches another name's copies."""
        db = _open_db(tmp_path / "core.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(mode=0o700)
        (backup_dir / "backup_runtime_20260101_000000.db").write_bytes(b"x")
        deleted = prune_old_backups(
            {"core": db}, str(tmp_path / "core.db"), keep=0
        )
        assert deleted == 0
        db.close()


class TestLoopHeartbeats:
    """Heartbeat write/read operations round-trip through metadata."""

    @pytest.mark.asyncio
    async def test_heartbeat_roundtrip(self, tmp_path):
        """A recorded heartbeat comes back with its loop name."""
        db = _open_db(tmp_path / "hb.db")
        record = make_record_loop_heartbeat_handler(db)
        read = make_get_loop_heartbeats_handler(db)
        result = await record({"loop_name": "pruning"})
        assert result["ok"] is True
        heartbeats = (await read({}))["heartbeats"]
        assert "pruning" in heartbeats
        assert heartbeats["pruning"].startswith("20")
        db.close()

    @pytest.mark.asyncio
    async def test_unknown_loop_name_rejected(self, tmp_path):
        """Arbitrary loop names cannot pollute the metadata keyspace."""
        db = _open_db(tmp_path / "hb.db")
        record = make_record_loop_heartbeat_handler(db)
        result = await record({"loop_name": "evil"})
        assert result["ok"] is False
        heartbeats = (
            await make_get_loop_heartbeats_handler(db)({})
        )["heartbeats"]
        assert heartbeats == {}
        db.close()
