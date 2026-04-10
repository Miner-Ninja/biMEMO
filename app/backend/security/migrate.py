"""Migration CLI: convert Argon2id-encrypted key file to plain keyfile.

Reads the existing password-encrypted key file, prompts for the unlock
password, decrypts the master key, and writes the raw 32-byte key to
a new file with chmod 600. This enables migration from the old
setup/unlock flow to the new direct-keyfile approach.
"""

import getpass
import logging
import os
import stat
import sys
from pathlib import Path


logger = logging.getLogger(__name__)

EXPECTED_MASTER_KEY_LENGTH = 32


def _exit_with_error(message: str) -> None:
    """Print one migration error and exit with status 1."""
    print(message, file=sys.stderr)
    sys.exit(1)


def _required_password(password: str | None) -> str:
    """Return the supplied password or prompt for it interactively."""
    if password is not None:
        return password
    return getpass.getpass("Unlock password: ")


def _invalid_master_key_length_error(length: int) -> str:
    """Build the standard migration error for an unexpected key length."""
    return f"Error: unexpected key length {length}"


def _write_plain_keyfile(output: Path, master_key: bytes) -> None:
    """Write the decrypted master key atomically with chmod 600."""
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(output) + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(master_key)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.rename(tmp_path, str(output))
    except OSError as error:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _exit_with_error(f"Error writing key file: {error}")


def migrate_to_keyfile(
    encrypted_path: str,
    output_path: str,
    password: str | None = None,
) -> None:
    """Decrypt an Argon2id-encrypted key file and write plain keyfile.

    Reads the encrypted key file at encrypted_path, decrypts
    using the provided password, and writes the raw 32-byte
    master key to output_path with chmod 600.

    Args:
        encrypted_path: Path to the Argon2id-encrypted key file.
        output_path: Path for the output plain key file.
        password: Unlock password. If None, prompts interactively.

    Raises:
        SystemExit: On any error (wrong password, file not found, etc).
    """
    from backend.security.encrypted_key_file import (
        KeyFileError,
        WrongPasswordError,
        decrypt_master_key,
        read_key_file,
    )

    # Read encrypted key file
    try:
        encrypted_blob = read_key_file(encrypted_path)
    except KeyFileError as error:
        _exit_with_error(f"Error reading key file: {error}")

    password = _required_password(password)

    try:
        master_key = decrypt_master_key(encrypted_blob, password)
    except WrongPasswordError:
        _exit_with_error("Error: wrong password")
    except KeyFileError as error:
        _exit_with_error(f"Error decrypting: {error}")

    if len(master_key) != EXPECTED_MASTER_KEY_LENGTH:
        _exit_with_error(_invalid_master_key_length_error(len(master_key)))

    output = Path(output_path)
    _write_plain_keyfile(output, master_key)

    print(f"Migration complete: {output_path}")
    print("File permissions: 600 (owner read/write only)")
    print(
        "Update your .env: BIMEMO_KEY_SOURCE=file, "
        f"BIMEMO_KEY_FILE_PATH={output_path}"
    )


def main() -> None:
    """CLI entry point for key file migration."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="bimemo-migrate-to-keyfile",
        description=(
            "Convert Argon2id-encrypted key file to plain "
            "32-byte keyfile for the new multi-process architecture."
        ),
    )
    parser.add_argument(
        "encrypted_path",
        help="Path to the existing Argon2id-encrypted key file",
    )
    parser.add_argument(
        "output_path",
        help="Path for the output plain 32-byte key file",
    )
    parser.add_argument(
        "--password",
        help="Unlock password (prompts if not provided)",
        default=None,
    )

    args = parser.parse_args()
    migrate_to_keyfile(
        args.encrypted_path,
        args.output_path,
        args.password,
    )


if __name__ == "__main__":
    main()
