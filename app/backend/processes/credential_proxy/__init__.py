"""Credential Proxy subpackage — re-exports all public symbols."""

from .main import (
    CREDENTIAL_OPERATIONS,
    credential_proxy_main,
)


__all__ = [
    "CREDENTIAL_OPERATIONS",
    "credential_proxy_main",
]
