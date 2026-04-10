"""Tests for the database reset preparation helper."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_db_reset.py"
MODULE_NAME = "prepare_db_reset"


def _load_script_module():
    """Load the helper script as an importable module."""
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]

    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


module = _load_script_module()


class TestPrepareDatabaseReset:
    """Verify backup naming, rotation, and deletion safeguards."""

    def test_build_backup_path_uses_human_readable_timestamp(self):
        """Backup filename should contain a readable date and time."""
        backup_path = module.build_backup_path(
            "/data/db/bimemo.db",
            now=datetime(2026, 3, 15, 18, 30, 45, tzinfo=UTC),
        )

        assert backup_path == Path("/data/db/bimemo_backup_2026-03-15_18-30-45.db")

    def test_rotate_backups_keeps_only_five_newest(self, tmp_path):
        """Rotation should drop older backup files beyond the keep limit."""
        database_path = tmp_path / "bimemo.db"
        database_path.write_text("db")

        backup_paths: list[Path] = []
        for index in range(6):
            backup_path = tmp_path / f"bimemo_backup_2026-03-15_00-00-0{index}.db"
            backup_path.write_text(str(index))
            backup_paths.append(backup_path)

        for index, backup_path in enumerate(backup_paths):
            modified_time = 100 + index
            module.os.utime(backup_path, (modified_time, modified_time))

        module.rotate_backups(str(database_path))

        remaining_names = sorted(path.name for path in tmp_path.glob("*.db"))

        assert remaining_names == [
            "bimemo.db",
            "bimemo_backup_2026-03-15_00-00-01.db",
            "bimemo_backup_2026-03-15_00-00-02.db",
            "bimemo_backup_2026-03-15_00-00-03.db",
            "bimemo_backup_2026-03-15_00-00-04.db",
            "bimemo_backup_2026-03-15_00-00-05.db",
        ]

    def test_require_delete_confirmation_rejects_missing_confirmation(
        self,
        monkeypatch,
    ):
        """Deletion should fail when confirmation was not provided."""
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)

        with pytest.raises(RuntimeError, match="Database deletion was not confirmed"):
            module.require_delete_confirmation("")

    def test_prepare_database_file_reset_backs_up_before_delete(self, tmp_path):
        """Delete flow should leave a backup and remove the live database file."""
        database_path = tmp_path / "bimemo.db"
        database_path.write_text("live-db")

        backup_path = module.prepare_database_file_reset(
            str(database_path),
            confirmation_text=module.DELETE_CONFIRMATION_TEXT,
            now=datetime(2026, 3, 15, 18, 30, 45, tzinfo=UTC),
        )

        assert backup_path == (tmp_path / "bimemo_backup_2026-03-15_18-30-45.db")
        assert backup_path.read_text() == "live-db"
        assert not database_path.exists()

    def test_backup_database_file_rotates_after_copy(self, tmp_path):
        """Plain backup mode should still rotate old files."""
        database_path = tmp_path / "bimemo.db"
        database_path.write_text("live-db")

        old_backup = tmp_path / "bimemo_backup_2026-03-15_18-00-00.db"
        old_backup.write_text("old")

        older_backup = tmp_path / "bimemo_backup_2026-03-15_17-00-00.db"
        older_backup.write_text("older")

        oldest_backup = tmp_path / "bimemo_backup_2026-03-15_16-00-00.db"
        oldest_backup.write_text("oldest")

        oldest_backup_2 = tmp_path / "bimemo_backup_2026-03-15_15-00-00.db"
        oldest_backup_2.write_text("oldest-2")

        oldest_backup_3 = tmp_path / "bimemo_backup_2026-03-15_14-00-00.db"
        oldest_backup_3.write_text("oldest-3")

        for index, path in enumerate(
            [
                old_backup,
                older_backup,
                oldest_backup,
                oldest_backup_2,
                oldest_backup_3,
            ],
            start=1,
        ):
            module.os.utime(path, (100 + index, 100 + index))

        backup_path = module.backup_database_file(
            str(database_path),
            now=datetime(2026, 3, 15, 19, 0, 0, tzinfo=UTC),
        )

        assert backup_path.exists()
        remaining_backups = sorted(
            path.name for path in tmp_path.glob("bimemo_backup_*.db")
        )
        assert len(remaining_backups) == 5
        assert "bimemo_backup_2026-03-15_18-00-00.db" not in remaining_backups
