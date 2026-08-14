"""Deterministic safety and registry-owned permission decisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from backend.tools.registry import (
    PermissionMode,
    PermissionReply,
    PermissionScope,
)
from backend.tools.validation import (
    ValidationError,
    check_path_traversal,
    check_sensitive_path,
    check_sensitive_read_path,
    validate_url,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.tools.broker import PreparedInvocation
    from backend.tools.registry import ToolRegistry


class DecisionKind(StrEnum):
    """Policy decision for one assistant tool action."""

    ALLOW = "allow"
    ASK_ONCE = "ask_once"
    ASK_SCOPED = "ask_scoped"
    PREVIEW_REQUIRED = "preview_required"
    BLOCK = "block"


@dataclass(frozen=True)
class ActionContext:
    """Authenticated context surrounding one prepared invocation."""

    conversation_id: str
    channel: str
    session_id: str
    tool_name: str
    parameters: dict
    tool_policy_version: str = "registry-v1"
    prepared: PreparedInvocation | None = None


@dataclass(frozen=True)
class ActionDecision:
    """Result of deterministic safety and permission evaluation."""

    kind: DecisionKind
    reason: str = ""
    details: dict[str, object] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """Whether execution may proceed immediately."""
        return self.kind is DecisionKind.ALLOW


_DESTRUCTIVE_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|[;&|]\s*)rm\s+(-[^\s]*r[^\s]*f|-rf|-fr)\s+(/|~)(\s|$)"),
    re.compile(r"(^|[;&|]\s*)mkfs(?:\.[^\s]+)?\s+"),
    re.compile(r"(^|[;&|]\s*)dd\s+.*\bof=/dev/(sd|hd|vd|nvme|mapper/)"),
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
)


class ActionDecisionService:
    """Resolve policy from the finalized registry after one typed validation."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        workspace_roots: Callable[[], list[str]] | None = None,
    ) -> None:
        """Initialize with the sole registry and live workspace roots."""
        self.registry = registry
        self.workspace_roots = workspace_roots or (lambda: [])

    def decide(self, context: ActionContext) -> ActionDecision:
        """Return a fail-closed decision for one action context."""
        try:
            return self._decide_or_raise(context)
        except (ValidationError, ValueError) as exc:
            message = str(exc)
            source = (
                "policy_version"
                if "policy version" in message.lower()
                else "registry_policy"
                if "tool policy" in message.lower()
                or "prepared invocation" in message.lower()
                else "hard_safety"
            )
            definition = self.registry.get(context.tool_name)
            return ActionDecision(
                DecisionKind.BLOCK,
                message,
                {
                    "permission_mode": "blocked",
                    "policy_revision": (
                        definition.permission.revision if definition else ""
                    ),
                    "source": source,
                },
            )

    def _prepared(self, context: ActionContext) -> PreparedInvocation:
        if context.prepared is not None:
            if context.prepared.tool_name != context.tool_name:
                raise ValidationError("Prepared invocation tool mismatch")
            return context.prepared
        definition = self.registry.get(context.tool_name)
        if definition is None:
            raise ValidationError(f"Unknown tool policy: {context.tool_name}")

        raise ValidationError("Permission decision requires a prepared invocation")

    def _decide_or_raise(self, context: ActionContext) -> ActionDecision:
        if context.tool_policy_version != "registry-v1":
            raise ValidationError("Unsupported tool policy version")
        prepared = self._prepared(context)
        params = dict(prepared.canonical_parameters)
        try:
            self._run_hard_checks(prepared.tool_name, params)
        except ValidationError as exc:
            return ActionDecision(
                DecisionKind.BLOCK,
                str(exc),
                {
                    "permission_mode": "blocked",
                    "policy_revision": prepared.definition.permission.revision,
                    "source": "hard_safety",
                },
            )

        policy = prepared.definition.permission
        mode = policy.resolve(prepared.typed_input)
        if mode is PermissionMode.BLOCKED:
            return ActionDecision(
                DecisionKind.BLOCK,
                "Capability is blocked by policy",
                {
                    "permission_mode": "blocked",
                    "policy_revision": policy.revision,
                    "source": "registry_policy",
                },
            )

        workspace_root = self._workspace_root(policy.workspace_argument, params)
        scope = self._scope(
            context=context,
            prepared=prepared,
            mode=mode,
            workspace_root=workspace_root,
        )
        canonical_scope = json.dumps(
            scope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        details: dict[str, object] = {
            "permission_mode": mode.value,
            "reply_mode": policy.reply.value,
            "scope": scope,
            "scope_hash": hashlib.sha256(canonical_scope.encode()).hexdigest(),
            "policy_revision": policy.revision,
            "source": "registry_policy",
            "read_only": policy.reply is PermissionReply.LLM_CLASSIFIED,
            "scheduler_preauthorizable": policy.scheduler_preauthorizable,
        }
        if mode is PermissionMode.IMPLICIT:
            return ActionDecision(
                DecisionKind.ALLOW,
                "Allowed by registry policy",
                details,
            )
        if mode is PermissionMode.CONVERSATION_CAPABILITY:
            return ActionDecision(
                DecisionKind.ASK_ONCE,
                "Capability needs conversation approval",
                details,
            )
        if mode is PermissionMode.SCOPED:
            return ActionDecision(
                DecisionKind.ASK_SCOPED,
                "Capability scope needs approval",
                details,
            )
        return ActionDecision(
            DecisionKind.ASK_ONCE,
            "Invocation needs explicit approval",
            details,
        )

    def _workspace_root(self, argument: str, params: dict) -> str:
        if not argument:
            return ""
        raw_target = str(params.get(argument, "")).strip()
        if not raw_target:
            raise ValidationError(f"{argument} must be a non-empty workspace path")
        target = Path(raw_target).expanduser().resolve(strict=False)
        candidates: list[Path] = []
        for raw_root in self.workspace_roots():
            root = Path(raw_root).expanduser().resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError:
                continue
            candidates.append(root)
        if not candidates:
            raise ValidationError("Target is outside every allowed workspace")
        return str(max(candidates, key=lambda path: len(path.parts)))

    @staticmethod
    def _scope(
        *,
        context: ActionContext,
        prepared: PreparedInvocation,
        mode: PermissionMode,
        workspace_root: str,
    ) -> dict[str, object]:
        base: dict[str, object] = {
            "conversation_id": context.conversation_id,
            "channel": context.channel,
            "tool_name": prepared.tool_name,
        }
        if mode is PermissionMode.CONVERSATION_CAPABILITY:
            return base
        if mode is PermissionMode.SCOPED:
            if not workspace_root:
                raise ValidationError("Scoped action has no canonical workspace")
            return {**base, "workspace_root": workspace_root}
        if mode is PermissionMode.PER_INVOCATION:
            return {
                **base,
                "arguments_hash": prepared.canonical_hash,
                **({"workspace_root": workspace_root} if workspace_root else {}),
            }
        workspace_scope = {"workspace_root": workspace_root} if workspace_root else {}
        return {**base, **workspace_scope}

    def _run_hard_checks(self, tool_name: str, params: dict) -> None:
        if tool_name == "file_read":
            self._check_read_path(str(params.get("path", "")))
        elif tool_name == "file_write":
            self._check_write_path(str(params.get("path", "")))
        elif tool_name == "file_search":
            self._check_read_path(str(params.get("directory", "")))
            check_path_traversal(str(params.get("pattern", "")))
        elif tool_name in {"http_fetch", "web_search"}:
            self._check_url_params(tool_name, params)
        elif tool_name in {"execute_command", "execute_command_stream"}:
            self._check_command(str(params.get("command", "")))

    @staticmethod
    def _check_read_path(path: str) -> None:
        check_path_traversal(path)
        check_sensitive_read_path(path)

    @staticmethod
    def _check_write_path(path: str) -> None:
        check_path_traversal(path)
        check_sensitive_path(path)

    @staticmethod
    def _check_url_params(tool_name: str, params: dict) -> None:
        if tool_name == "http_fetch":
            url = str(params.get("url", ""))
            if url:
                validate_url(url)
            return
        query = str(params.get("query", ""))
        parsed = urlparse(query)
        if parsed.scheme and parsed.netloc:
            validate_url(query)

    @staticmethod
    def _check_command(command: str) -> None:
        normalized = command.strip().lower()
        for pattern in _DESTRUCTIVE_COMMAND_PATTERNS:
            if pattern.search(normalized):
                raise ValidationError("Destructive command blocked")


__all__ = [
    "ActionContext",
    "ActionDecision",
    "ActionDecisionService",
    "DecisionKind",
    "PermissionMode",
    "PermissionReply",
    "PermissionScope",
]
