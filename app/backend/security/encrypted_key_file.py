"""Encrypted key file using Argon2id key derivation and AES-256-GCM.

Handles encryption and decryption of the master key using a user password.
File format: Argon2id salt (16 bytes) + Argon2 params (12 bytes) +
AES-GCM nonce (12 bytes) + ciphertext (32 bytes) + GCM tag (16 bytes).
Uses atomic file writes to prevent corruption on crash.
"""

import logging
import os
import struct
import tempfile
from pathlib import Path

import argon2.low_level
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


logger = logging.getLogger(__name__)

# --- Argon2id default parameters ---
ARGON2_MEMORY_KB = 65536  # 64 MB
ARGON2_ITERATIONS = 3
ARGON2_PARALLELISM = 4
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

# --- AES-GCM parameters ---
AES_NONCE_LEN = 12
AES_KEY_LEN = 32
AES_TAG_LEN = 16

# --- Struct format for Argon2 params ---
# (memory_kb: uint32, iterations: uint32, parallelism: uint32)
ARGON2_PARAMS_FORMAT = "<III"
ARGON2_PARAMS_LEN = struct.calcsize(ARGON2_PARAMS_FORMAT)


def _expected_key_file_size() -> int:
    """Return the exact byte size of one encrypted key file."""
    return (
        ARGON2_SALT_LEN + ARGON2_PARAMS_LEN + AES_NONCE_LEN + AES_KEY_LEN + AES_TAG_LEN
    )


def _parse_encrypted_blob(
    encrypted_blob: bytes,
) -> tuple[bytes, int, int, int, bytes, bytes]:
    """Split one encrypted key blob into salt, params, nonce, and ciphertext."""
    expected_len = _expected_key_file_size()
    if len(encrypted_blob) != expected_len:
        actual = len(encrypted_blob)
        msg = f"Invalid key file size: expected {expected_len}, got {actual}"
        raise KeyFileError(msg)

    offset = 0
    salt = encrypted_blob[offset : offset + ARGON2_SALT_LEN]
    offset += ARGON2_SALT_LEN

    memory_kb, iterations, parallelism = struct.unpack(
        ARGON2_PARAMS_FORMAT,
        encrypted_blob[offset : offset + ARGON2_PARAMS_LEN],
    )
    offset += ARGON2_PARAMS_LEN

    nonce = encrypted_blob[offset : offset + AES_NONCE_LEN]
    offset += AES_NONCE_LEN

    ciphertext_with_tag = encrypted_blob[offset:]
    return (
        salt,
        memory_kb,
        iterations,
        parallelism,
        nonce,
        ciphertext_with_tag,
    )


def _write_atomic(target: Path, encrypted_blob: bytes) -> None:
    """Write one encrypted key blob atomically to disk."""
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent))
    try:
        os.write(fd, encrypted_blob)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        os.unlink(tmp_path)
        raise
    os.close(fd)
    os.rename(tmp_path, str(target))


def _key_file_path(path: str) -> Path:
    """Return one normalized filesystem path for the encrypted key file."""
    return Path(path)


class KeyFileError(Exception):
    """Raised when key file operations fail."""


class WrongPasswordError(KeyFileError):
    """Raised when decryption fails due to wrong password."""


class TamperedFileError(KeyFileError):
    """Raised when the key file has been tampered with."""


def derive_key(
    password: str,
    salt: bytes,
    memory_kb: int = ARGON2_MEMORY_KB,
    iterations: int = ARGON2_ITERATIONS,
    parallelism: int = ARGON2_PARALLELISM,
) -> bytes:
    """Derive an encryption key from password using Argon2id.

    Args:
        password: User's unlock password.
        salt: Random salt for key derivation.
        memory_kb: Argon2id memory parameter in KB.
        iterations: Argon2id time cost.
        parallelism: Argon2id parallelism degree.

    Returns:
        32-byte derived key.
    """
    return argon2.low_level.hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=iterations,
        memory_cost=memory_kb,
        parallelism=parallelism,
        hash_len=ARGON2_HASH_LEN,
        type=argon2.low_level.Type.ID,
    )


def encrypt_master_key(
    master_key: bytes,
    password: str,
    memory_kb: int = ARGON2_MEMORY_KB,
    iterations: int = ARGON2_ITERATIONS,
    parallelism: int = ARGON2_PARALLELISM,
) -> bytes:
    """Encrypt a master key with a password.

    Args:
        master_key: 32-byte master key to encrypt.
        password: User's unlock password.
        memory_kb: Argon2id memory parameter in KB.
        iterations: Argon2id time cost.
        parallelism: Argon2id parallelism degree.

    Returns:
        Encrypted key file bytes (salt + params + nonce + ciphertext + tag).
    """
    salt = os.urandom(ARGON2_SALT_LEN)
    derived = derive_key(password, salt, memory_kb, iterations, parallelism)

    nonce = os.urandom(AES_NONCE_LEN)
    aesgcm = AESGCM(derived)
    ciphertext_with_tag = aesgcm.encrypt(nonce, master_key, None)

    params = struct.pack(ARGON2_PARAMS_FORMAT, memory_kb, iterations, parallelism)
    return salt + params + nonce + ciphertext_with_tag


def decrypt_master_key(encrypted_blob: bytes, password: str) -> bytes:
    """Decrypt a master key from the encrypted key file data.

    Args:
        encrypted_blob: Full encrypted key file bytes.
        password: User's unlock password.

    Returns:
        32-byte decrypted master key.

    Raises:
        WrongPasswordError: If the password is incorrect.
        TamperedFileError: If the GCM tag validation fails.
        KeyFileError: If the file format is invalid.
    """
    (
        salt,
        memory_kb,
        iterations,
        parallelism,
        nonce,
        ciphertext_with_tag,
    ) = _parse_encrypted_blob(encrypted_blob)

    derived = derive_key(password, salt, memory_kb, iterations, parallelism)
    aesgcm = AESGCM(derived)

    try:
        return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except InvalidTag as e:
        raise TamperedFileError("Key file integrity check failed") from e
    except Exception as e:
        raise WrongPasswordError("Invalid password") from e


def write_key_file(path: str, encrypted_blob: bytes) -> None:
    """Write encrypted key file atomically.

    Uses write-to-temp + fsync + rename to prevent corruption.

    Args:
        path: Destination path for the key file.
        encrypted_blob: Encrypted key data to write.
    """
    target = _key_file_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(target, encrypted_blob)
    logger.info("Encrypted key file written to %s", path)


def read_key_file(path: str) -> bytes:
    """Read encrypted key file from disk.

    Args:
        path: Path to the encrypted key file.

    Returns:
        Raw encrypted key file bytes.

    Raises:
        KeyFileError: If the file cannot be read.
    """
    target = _key_file_path(path)
    try:
        return target.read_bytes()
    except FileNotFoundError as e:
        raise KeyFileError(f"Key file not found: {path}") from e
    except OSError as e:
        raise KeyFileError(f"Cannot read key file: {e}") from e
