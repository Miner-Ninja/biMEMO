#!/usr/bin/env python3
"""Prepare a safe full database deletion by creating a backup first.

This helper is intentionally separate from memory_loop_test.py.
It is meant for development-time clean resets, where the operator wants to:

1. make a timestamped backup of the live database file;
2. keep only a limited number of recent backup files;
3. require an explicit confirmation before deleting the live database file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_DATABASE_PATH = os.getenv("BIMEMO_DATABASE_PATH", "/data/db/bimemo.db")
DEFAULT_KEEP_BACKUPS = 5
DELETE_CONFIRMATION_TEXT = "BACKUP-AND-DELETE"


def build_backup_path(database_path: str, *, now: datetime | None = None) -> Path:
    """Build a human-readable backup filename next to the live database file."""
    source_path = Path(database_path)
    timestamp = (now or datetime.now(tz=UTC)).strftime("%Y-%m-%d_%H-%M-%S")
    suffix = source_path.suffix or ".db"
    backup_name = f"{source_path.stem}_backup_{timestamp}{suffix}"
    return source_path.with_name(backup_name)


def rotate_backups(
    database_path: str,
    *,
    keep_count: int = DEFAULT_KEEP_BACKUPS,
) -> None:
    """Keep only the newest database backup files."""
    source_path = Path(database_path)
    suffix = source_path.suffix or ".db"
    backup_pattern = f"{source_path.stem}_backup_*{suffix}"
    backup_files = sorted(
        source_path.parent.glob(backup_pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale_backup_path in backup_files[keep_count:]:
        stale_backup_path.unlink(missing_ok=True)


def backup_database_file(
    database_path: str,
    *,
    keep_count: int = DEFAULT_KEEP_BACKUPS,
    now: datetime | None = None,
) -> Path:
    """Create a backup of the live database file and rotate old copies."""
    source_path = Path(database_path)
    if not source_path.exists():
        raise RuntimeError(
            "Cannot back up the database because the file does not exist: "
            f"{source_path}"
        )
    backup_path = build_backup_path(database_path, now=now)
    shutil.copy2(source_path, backup_path)
    rotate_backups(database_path, keep_count=keep_count)
    return backup_path


def require_delete_confirmation(confirmation_text: str) -> None:
    """Require explicit confirmation before deleting the live database file."""
    if confirmation_text == DELETE_CONFIRMATION_TEXT:
        return

    prompt = (
        "This will create a timestamped backup and then delete the live "
        "database file.\n"
        f"Type {DELETE_CONFIRMATION_TEXT!r} to continue: "
    )
    if sys.stdin.isatty():
        entered_text = input(prompt).strip()
        if entered_text == DELETE_CONFIRMATION_TEXT:
            return

    raise RuntimeError(
        "Database deletion was not confirmed. Re-run with "
        f"--confirm-delete-db {DELETE_CONFIRMATION_TEXT!r} "
        "or type the same text at the prompt."
    )


def prepare_database_file_reset(
    database_path: str,
    *,
    confirmation_text: str,
    keep_count: int = DEFAULT_KEEP_BACKUPS,
    now: datetime | None = None,
) -> Path:
    """Back up the live database file and then delete it."""
    require_delete_confirmation(confirmation_text)
    backup_path = backup_database_file(
        database_path,
        keep_count=keep_count,
        now=now,
    )
    Path(database_path).unlink()
    return backup_path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Back up and optionally delete the live biMEMO database file.",
    )
    parser.add_argument(
        "--database-path",
        default=DEFAULT_DATABASE_PATH,
        help="Path to the live database file.",
    )
    parser.add_argument(
        "--keep-backups",
        type=int,
        default=DEFAULT_KEEP_BACKUPS,
        help="How many newest backup files to keep.",
    )
    parser.add_argument(
        "--delete-db-file",
        action="store_true",
        help="Delete the live database file after the backup is created.",
    )
    parser.add_argument(
        "--confirm-delete-db",
        default="",
        help="Required confirmation text for --delete-db-file in non-interactive runs.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the requested backup or backup-and-delete workflow."""
    arguments = parse_args()
    if arguments.delete_db_file:
        backup_path = prepare_database_file_reset(
            arguments.database_path,
            confirmation_text=arguments.confirm_delete_db,
            keep_count=arguments.keep_backups,
        )
        print(f"Created backup: {backup_path}")
        print(f"Deleted live database file: {arguments.database_path}")
        return 0

    backup_path = backup_database_file(
        arguments.database_path,
        keep_count=arguments.keep_backups,
    )
    print(f"Created backup: {backup_path}")
    print("Live database file was kept in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
