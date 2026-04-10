"""Security subsystem for encryption, content filtering, and audit logging.

Manages the password-encrypted master key lifecycle, SQLCipher database
access, content filtering for credential detection and prompt injection,
and append-only audit logging with hash chain integrity verification.
"""
