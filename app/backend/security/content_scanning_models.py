"""Data models for content filtering results.

Defines the FilterResult dataclass returned by content scanning
operations. Separated from content_scanning.py to allow importing
the result type without loading regex pattern compilation and
filter logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FilterResult:
    """Result of content filtering.

    Attributes:
        text: Filtered text (with redactions).
        credentials_found: List of credential types found.
        injection_flagged: Whether injection was detected.
        pii_found: Whether PII was detected.
        code_execution: Whether code execution attempt detected.
        suspicious: Whether suspicious content detected.
        blocked: Whether the content should be blocked.
        warnings: List of warning messages.
    """

    text: str
    credentials_found: list[str] = field(default_factory=list)
    injection_flagged: bool = False
    pii_found: bool = False
    code_execution: bool = False
    suspicious: bool = False
    blocked: bool = False
    warnings: list[str] = field(default_factory=list)
