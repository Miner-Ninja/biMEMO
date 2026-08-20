"""Self-describing typed errors shared across process boundaries.

Every class in this module carries a stable machine-readable ``code``
(snake_case, following the terminal-error convention).  The code — not
the message — is the contract: handlers raise these where a failure has
a stable meaning, the IPC layer propagates the code alongside the
human-readable message, and consumers classify by code instead of
matching message text.  End-user text is derived from the code via
:func:`user_safe_error_text`, never from the raw error.

This module has no application imports: it is safe to import from any
process without circular-import risk.
"""

from __future__ import annotations


# --- Stable codes for conditions that are labeled, not raised -----------

#: Signing-proxy setup refusal.  The signing handler labels the DB result
#: directly (it never raises), so the code is a shared constant.
PASSWORD_ALREADY_EXISTS_CODE = "password_already_exists"  # noqa: S105 — error-code constant name, not a credential

#: Code assigned by the DB Proxy clients to transport-level failures.
DBPROXY_ERROR_CODE = "dbproxy_error"
DBPROXY_TIMEOUT_CODE = "dbproxy_timeout"
DBPROXY_IPC_CODE = "dbproxy_ipc"

#: Code for a malformed IPC envelope (type check in the server handler).
IPC_MALFORMED_CODE = "ipc_malformed"

#: Fallback code for an error that carries no typed code of its own.
FALLBACK_ERROR_CODE = "internal_error"


# --- Typed error classes -------------------------------------------------


class TypedIPCError(Exception):
    """A self-describing typed IPC error.

    Subclasses set ``code`` to a stable snake_case identifier.  The
    message is human-readable detail intended for logs, never for end
    users.
    """

    code: str = "ipc_unexpected"


class OperationNotFoundError(TypedIPCError):
    """Raised when an unknown operation is requested."""

    code = "operation_unknown"


class OperationNotAllowedError(TypedIPCError):
    """Raised when an operation is not allowed on the current channel."""

    code = "operation_not_allowed"


class ConfirmationNotFoundError(TypedIPCError, ValueError):
    """The referenced agent confirmation does not exist."""

    code = "confirmation_unknown"


class VectorDimensionMismatchError(TypedIPCError):
    """The stored vector table was created with different dimensions."""

    code = "vector_dimension_mismatch"


class SettingsBlobTooLargeError(TypedIPCError, ValueError):
    """A persisted settings blob exceeds the server-side byte cap."""

    code = "settings_blob_too_large"


# --- Code helpers --------------------------------------------------------


def error_code_of(error: BaseException) -> str:
    """Return the stable code for one error, falling back safely."""
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    return FALLBACK_ERROR_CODE


#: User-facing text for the codes that can reach an end user.  Text that
#: is only ever seen by an operator (admin 413, auth 409) is handled at
#: its route and does not belong here.
_USER_SAFE_MESSAGES: dict[str, str] = {
    DBPROXY_TIMEOUT_CODE: (
        "The database is temporarily unavailable. Please try again."
    ),
    DBPROXY_IPC_CODE: (
        "A connection to a background service was lost. Please try again."
    ),
    DBPROXY_ERROR_CODE: "Internal error. Please try again.",
    ConfirmationNotFoundError.code: (
        "This action is no longer available. Please start a new request."
    ),
    VectorDimensionMismatchError.code: (
        "The memory index needs to be rebuilt. Please try again."
    ),
}

_DEFAULT_USER_SAFE_TEXT = "Internal error. Please try again."


def user_safe_error_text(error: BaseException) -> str:
    """Derive short user-facing text from the error code.

    The raw error text can carry SQL fragments, socket paths, or
    third-party internals; none of that reaches the end user.
    """
    return _USER_SAFE_MESSAGES.get(error_code_of(error), _DEFAULT_USER_SAFE_TEXT)
