"""CLI tools for offline key rotation and password change.

Provides command-line utilities for rotating the master key (re-encrypting
the database with a new key) and changing the unlock password without
modifying the database. Both operations run offline with the container
stopped and increment the session generation counter.
"""

import argparse
import getpass
import logging
import secrets
import sys

import sqlcipher3

from backend.security.encrypted_key_file import (
    decrypt_master_key,
    encrypt_master_key,
    read_key_file,
    write_key_file,
)
from backend.security.master_key import MASTER_KEY_LENGTH
from backend.security.setup import _validate_password_strength


logger = logging.getLogger(__name__)

# --- Session counter table ---
SESSION_COUNTER_TABLE = """
CREATE TABLE IF NOT EXISTS session_counter (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    generation INTEGER NOT NULL DEFAULT 0
)
"""


def _prompt_new_password() -> str:
    """Prompt for a new password twice and verify both entries match."""
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Confirm new password: ")
    if first != second:
        print("Passwords do not match")
        sys.exit(1)
    return first


def _current_and_new_passwords() -> tuple[str, str]:
    """Prompt for the current password and one confirmed new password."""
    return getpass.getpass("Current password: "), _prompt_new_password()


def _open_cipher_connection(db_path: str, master_key: bytes) -> sqlcipher3.Connection:
    """Open one SQLCipher connection with the given key."""
    hex_key = master_key.hex()
    conn = sqlcipher3.connect(db_path)
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\"")
    return conn


def _read_master_key(key_file_path: str, password: str) -> bytes:
    """Read and decrypt the stored master key."""
    encrypted_blob = read_key_file(key_file_path)
    return decrypt_master_key(encrypted_blob, password)


def _argon2_kwargs(
    argon2_memory: int | None,
    argon2_iterations: int | None,
    argon2_parallelism: int | None,
) -> dict[str, int]:
    """Build optional Argon2 override keyword arguments."""
    kwargs: dict[str, int] = {}
    if argon2_memory is not None:
        kwargs["memory_kb"] = argon2_memory
    if argon2_iterations is not None:
        kwargs["iterations"] = argon2_iterations
    if argon2_parallelism is not None:
        kwargs["parallelism"] = argon2_parallelism
    return kwargs


def _increment_session_counter(db_path: str, master_key: bytes) -> None:
    """Increment the session generation counter in the database.

    Args:
        db_path: Path to the SQLCipher database.
        master_key: Decrypted master key for database access.
    """
    conn = _open_cipher_connection(db_path, master_key)
    try:
        conn.execute(SESSION_COUNTER_TABLE)
        conn.execute(
            "INSERT INTO session_counter (id, generation) VALUES (1, 1) "
            "ON CONFLICT(id) DO UPDATE SET generation = generation + 1"
        )
        conn.commit()
        cursor = conn.execute("SELECT generation FROM session_counter WHERE id = 1")
        generation = cursor.fetchone()[0]
    finally:
        conn.close()
    logger.info("Session generation counter incremented to %d", generation)


def rotate_key(
    key_file_path: str, db_path: str, old_password: str, new_password: str
) -> None:
    """Rotate the master key: decrypt old, generate new, PRAGMA rekey, re-encrypt.

    Args:
        key_file_path: Path to the encrypted key file.
        db_path: Path to the SQLCipher database.
        old_password: Current unlock password.
        new_password: New unlock password.

    Raises:
        SetupError: If new password is too weak.
    """
    _validate_password_strength(new_password)

    old_key = _read_master_key(key_file_path, old_password)

    new_key = secrets.token_bytes(MASTER_KEY_LENGTH)
    new_hex = new_key.hex()

    conn = _open_cipher_connection(db_path, old_key)
    try:
        conn.execute(f"PRAGMA rekey = \"x'{new_hex}'\"")
    finally:
        conn.close()

    encrypted_new = encrypt_master_key(new_key, new_password)
    write_key_file(key_file_path, encrypted_new)

    _increment_session_counter(db_path, new_key)
    logger.info("Master key rotated successfully")


def change_password(
    key_file_path: str,
    db_path: str,
    old_password: str,
    new_password: str,
    argon2_memory: int | None = None,
    argon2_iterations: int | None = None,
    argon2_parallelism: int | None = None,
) -> None:
    """Change unlock password without rotating the master key.

    Args:
        key_file_path: Path to the encrypted key file.
        db_path: Path to the SQLCipher database.
        old_password: Current unlock password.
        new_password: New unlock password.
        argon2_memory: Optional Argon2id memory override in KB.
        argon2_iterations: Optional Argon2id iterations override.
        argon2_parallelism: Optional Argon2id parallelism override.

    Raises:
        SetupError: If new password is too weak.
    """
    _validate_password_strength(new_password)

    master_key = _read_master_key(key_file_path, old_password)

    kwargs = _argon2_kwargs(argon2_memory, argon2_iterations, argon2_parallelism)
    encrypted_new = encrypt_master_key(master_key, new_password, **kwargs)
    write_key_file(key_file_path, encrypted_new)

    _increment_session_counter(db_path, master_key)
    logger.info("Password changed successfully")


def main() -> None:  # pragma: no cover
    """CLI entry point for key management tools."""
    parser = argparse.ArgumentParser(description="biMEMO key management")
    subparsers = parser.add_subparsers(dest="command")

    rotate_parser = subparsers.add_parser("rotate-key", help="Rotate the master key")
    rotate_parser.add_argument("--key-file", required=True)
    rotate_parser.add_argument("--db-path", required=True)

    change_parser = subparsers.add_parser(
        "change-password", help="Change unlock password"
    )
    change_parser.add_argument("--key-file", required=True)
    change_parser.add_argument("--db-path", required=True)
    change_parser.add_argument("--argon2-memory", type=int)
    change_parser.add_argument("--argon2-iterations", type=int)
    change_parser.add_argument("--argon2-parallelism", type=int)

    args = parser.parse_args()

    if args.command == "rotate-key":
        old_pw, new_pw = _current_and_new_passwords()
        rotate_key(args.key_file, args.db_path, old_pw, new_pw)

    elif args.command == "change-password":
        old_pw, new_pw = _current_and_new_passwords()
        change_password(
            args.key_file,
            args.db_path,
            old_pw,
            new_pw,
            argon2_memory=args.argon2_memory,
            argon2_iterations=args.argon2_iterations,
            argon2_parallelism=args.argon2_parallelism,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
