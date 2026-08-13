"""biMEMO-native action decision service for assistant tool calls.

This module is the first policy checkpoint before ToolBroker execution.
It intentionally starts small: only non-negotiable safety blocks are
enforced here, while the existing trust-zone confirmation flow remains in
place. Later grant persistence and resumable confirmations can extend this
service without changing ToolBroker.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from backend.tools.validation import (
    ValidationError,
    check_path_traversal,
    check_sensitive_path,
    check_sensitive_read_path,
    validate_url,
)


class DecisionKind(StrEnum):
    """Policy decision for one assistant tool action."""

    ALLOW = "allow"
    ASK_ONCE = "ask_once"
    ASK_SCOPED = "ask_scoped"
    PREVIEW_REQUIRED = "preview_required"
    BLOCK = "block"


class PermissionMode(StrEnum):
    """Backend-owned reusable permission behavior for one capability."""

    IMPLICIT = "implicit"
    CONVERSATION_CAPABILITY = "conversation_capability"
    SCOPED = "scoped"
    PER_INVOCATION = "per_invocation"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PermissionManifest:
    """Static permission declaration owned by backend code."""

    mode: PermissionMode
    read_only: bool = False
    scope_argument: str = ""


PERMISSION_POLICY_REVISION = "1"
PERMISSION_MANIFESTS: dict[str, PermissionManifest] = {
    "memory_recall": PermissionManifest(PermissionMode.IMPLICIT, read_only=True),
    "web_search": PermissionManifest(
        PermissionMode.CONVERSATION_CAPABILITY,
        read_only=True,
    ),
    "http_fetch": PermissionManifest(
        PermissionMode.CONVERSATION_CAPABILITY,
        read_only=True,
    ),
    "file_read": PermissionManifest(
        PermissionMode.SCOPED,
        read_only=True,
        scope_argument="path",
    ),
    "file_search": PermissionManifest(
        PermissionMode.SCOPED,
        read_only=True,
        scope_argument="directory",
    ),
    "file_write": PermissionManifest(PermissionMode.PER_INVOCATION),
    "email_send": PermissionManifest(PermissionMode.PER_INVOCATION),
    "schedule_task": PermissionManifest(PermissionMode.PER_INVOCATION),
    "run_command": PermissionManifest(PermissionMode.PER_INVOCATION),
    "project_command": PermissionManifest(PermissionMode.PER_INVOCATION),
}


@dataclass(frozen=True)
class ActionContext:
    """Context used to decide whether one tool call may proceed."""

    conversation_id: str
    channel: str
    session_id: str
    tool_name: str
    parameters: dict
    trust_zone: str = ""
    structured_authority: bool = False


@dataclass(frozen=True)
class ActionDecision:
    """Result of evaluating one action request."""

    kind: DecisionKind
    reason: str = ""
    details: dict[str, object] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """Return whether the tool may execute immediately."""
        return self.kind == DecisionKind.ALLOW


class ActionGrantReader(Protocol):
    """Read persisted user grants for action decisions."""

    def find_decision(
        self,
        *,
        conversation_id: str,
        channel: str,
        tool_name: str,
    ) -> str | None:
        """Return a stored decision such as ``allow`` or ``block``."""


_DESTRUCTIVE_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|[;&|]\s*)rm\s+(-[^\s]*r[^\s]*f|-rf|-fr)\s+(/|~)(\s|$)"),
    re.compile(r"(^|[;&|]\s*)mkfs(?:\.[^\s]+)?\s+"),
    re.compile(r"(^|[;&|]\s*)dd\s+.*\bof=/dev/(sd|hd|vd|nvme|mapper/)"),
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
)


class ActionDecisionService:
    """Decide whether assistant tool calls can execute.

    The service is deliberately deterministic and narrow at this stage. It
    blocks safety boundaries that should not be bypassed by policy changes:
    traversal, sensitive key paths, cloud metadata/blocked network fetches,
    and catastrophic shell commands.
    """

    def __init__(self, grants: ActionGrantReader | None = None) -> None:
        """Initialize the service with an optional grant reader."""
        self.grants = grants

    def decide(self, context: ActionContext) -> ActionDecision:
        """Return the policy decision for one tool call."""
        try:
            return self._decide_or_raise(context)
        except ValidationError as exc:
            return ActionDecision(DecisionKind.BLOCK, str(exc))

    def _decide_or_raise(self, context: ActionContext) -> ActionDecision:
        tool_name = context.tool_name
        params = context.parameters

        if tool_name == "file_read":
            self._check_read_path(str(params.get("path", "")))
        elif tool_name == "file_write":
            self._check_write_path(str(params.get("path", "")))
        elif tool_name == "file_search":
            self._check_read_path(str(params.get("directory", "")))
            check_path_traversal(str(params.get("pattern", "")))
        elif tool_name in {"http_fetch", "web_search"}:
            self._check_url_params(tool_name, params)
        elif tool_name in {"run_command", "project_command"}:
            self._check_command(str(params.get("command", "")))

        if not context.structured_authority:
            grant_decision = self._lookup_grant(context)
            if grant_decision == DecisionKind.ALLOW.value:
                return ActionDecision(
                    DecisionKind.ALLOW,
                    "Allowed by saved action grant",
                    {"source": "grant"},
                )
            if grant_decision == DecisionKind.BLOCK.value:
                return ActionDecision(
                    DecisionKind.BLOCK,
                    "Blocked by saved action grant",
                    {"source": "grant"},
                )
            if context.trust_zone == "red":
                return ActionDecision(
                    DecisionKind.ASK_ONCE,
                    "Sensitive action needs user approval",
                )
            return ActionDecision(
                DecisionKind.ALLOW,
                "Legacy trust-zone policy remains authoritative",
                {"source": "legacy_trust_zone"},
            )

        manifest = PERMISSION_MANIFESTS.get(
            tool_name,
            PermissionManifest(PermissionMode.PER_INVOCATION),
        )
        if manifest.mode is PermissionMode.BLOCKED:
            return ActionDecision(DecisionKind.BLOCK, "Capability is blocked by policy")
        details = self._permission_details(context, manifest)
        if manifest.mode is PermissionMode.IMPLICIT:
            return ActionDecision(
                DecisionKind.ALLOW,
                "Allowed by backend permission manifest",
                details,
            )

        grant_decision = self._lookup_grant(context)
        if grant_decision == DecisionKind.ALLOW.value:
            return ActionDecision(
                DecisionKind.ALLOW,
                "Allowed by saved action grant",
                {**details, "source": "legacy_grant"},
            )
        if grant_decision == DecisionKind.BLOCK.value:
            return ActionDecision(
                DecisionKind.BLOCK,
                "Blocked by saved action grant",
                {**details, "source": "legacy_grant"},
            )
        if manifest.mode is PermissionMode.CONVERSATION_CAPABILITY:
            return ActionDecision(
                DecisionKind.ASK_ONCE,
                "Capability needs conversation approval",
                details,
            )
        if manifest.mode is PermissionMode.SCOPED:
            return ActionDecision(
                DecisionKind.ASK_SCOPED,
                "Capability target needs scoped approval",
                details,
            )
        return ActionDecision(
            DecisionKind.ASK_ONCE,
            "Action needs per-invocation approval",
            details,
        )

    def _lookup_grant(self, context: ActionContext) -> str | None:
        if context.structured_authority or self.grants is None:
            return None
        return self.grants.find_decision(
            conversation_id=context.conversation_id,
            channel=context.channel,
            tool_name=context.tool_name,
        )

    def _permission_details(
        self,
        context: ActionContext,
        manifest: PermissionManifest,
    ) -> dict[str, object]:
        scope: dict[str, object]
        if manifest.mode is PermissionMode.CONVERSATION_CAPABILITY:
            scope = {"conversation_id": context.conversation_id}
        elif manifest.mode is PermissionMode.SCOPED:
            raw_target = str(context.parameters.get(manifest.scope_argument, ""))
            if not raw_target:
                raise ValidationError("Scoped action has no canonical target")
            scope = {
                "conversation_id": context.conversation_id,
                "target": str(Path(raw_target).expanduser().resolve(strict=False)),
            }
        else:
            scope = {
                "conversation_id": context.conversation_id,
                "arguments": context.parameters,
            }
        canonical = json.dumps(
            scope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return {
            "permission_mode": manifest.mode.value,
            "read_only": manifest.read_only,
            "scope": scope,
            "scope_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "policy_revision": PERMISSION_POLICY_REVISION,
        }

    def _check_read_path(self, path: str) -> None:
        check_path_traversal(path)
        check_sensitive_read_path(path)

    def _check_write_path(self, path: str) -> None:
        check_path_traversal(path)
        check_sensitive_path(path)

    def _check_url_params(self, tool_name: str, params: dict) -> None:
        if tool_name == "http_fetch":
            url = str(params.get("url", ""))
            if url:
                validate_url(url)
            return

        query = str(params.get("query", ""))
        parsed = urlparse(query)
        if parsed.scheme and parsed.netloc:
            validate_url(query)

    def _check_command(self, command: str) -> None:
        normalized = command.strip().lower()
        for pattern in _DESTRUCTIVE_COMMAND_PATTERNS:
            if pattern.search(normalized):
                raise ValidationError("Destructive command blocked")
