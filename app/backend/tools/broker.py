"""Tool broker: prepare, validate, finalize, and dispatch tool calls."""

import asyncio
import hashlib
import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError as PydanticError

from backend.tools.rate_limits import PersistentRateLimiter, ToolRateLimiter


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import BaseModel

    from backend.tools.registry import ToolDefinition, ToolRegistry


logger = logging.getLogger(__name__)

# Task-local execution context — see ToolBroker.current_context.
_EXECUTION_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "tool_execution_context", default=None
)


def _freeze_json(value: object) -> object:
    """Recursively freeze canonical JSON containers."""
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _bounded_value(value: object) -> object:
    """Bound nested observability values before redaction and persistence."""
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _bounded_value(item)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item) for item in value[:20]]
    return value


class ToolBrokerError(RuntimeError):
    """Raised when a tool call is rejected."""


@dataclass(frozen=True)
class PreparedInvocation:
    """One validated immutable invocation shared by policy and handler."""

    definition: ToolDefinition
    typed_input: BaseModel
    canonical_parameters: Mapping[str, object]
    canonical_json: str
    canonical_hash: str

    @property
    def tool_name(self) -> str:
        """Return the registered tool identity."""
        return self.definition.name


@dataclass(frozen=True)
class AuthorizationContext:
    """Non-secret decision context carried through execution observability."""

    permission_mode: str
    policy_revision: str
    source: str
    authorization_id: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Result of one authorized tool call.

    Attributes:
        tool_name: Name of the tool.
        success: Whether the call succeeded.
        result: Result data or error message.
        authorization: Backend authorization context.
        duration_ms: Execution time in milliseconds.
    """

    tool_name: str
    success: bool
    result: object
    duration_ms: float
    authorization: AuthorizationContext = field(
        default_factory=lambda: AuthorizationContext(
            "implicit", "registry-v1", "unspecified"
        )
    )


WORKSPACE_TOOL_NAMES = frozenset(
    {
        "file_read",
        "file_write",
        "file_search",
        "execute_command",
        "execute_command_stream",
        "git_status",
        "git_diff",
        "git_log",
        "git_commit",
        "git_push",
        "code_search",
    }
)


@dataclass
class ToolBroker:
    """Intercepts and routes tool calls.

    Finalizes definition-handler bindings, prepares typed invocations, checks
    rate limits, and dispatches already authorized calls.

    Attributes:
        registry: Tool registry for lookups.
        rate_limiter: Per-loop rate limiter (reset each agentic loop).
        persistent_limiter: Sliding-window limiter (survives across loops).
    """

    registry: ToolRegistry
    rate_limiter: ToolRateLimiter = field(default_factory=ToolRateLimiter)
    persistent_limiter: PersistentRateLimiter = field(
        default_factory=PersistentRateLimiter
    )
    audit_fn: Callable | None = None
    workspace_roots_fn: Callable[[], list[str]] | None = None
    _handlers: Mapping[str, Callable] = field(default_factory=dict)
    _finalized: bool = False
    _audit_tasks: set[asyncio.Task[object]] = field(default_factory=set)
    # Workspace action buffer — auto-collects workspace-relevant tool calls.
    _workspace_actions: list[dict] = field(default_factory=list)

    # Per-call execution context (e.g. conversation_id, session_id,
    # domain_grant_host). Task-local by design: sibling tool calls run
    # concurrently under asyncio.gather, and a security constraint such as
    # the granted host must never leak between calls or be clobbered by a
    # sibling's set_context. Handlers read self.broker.current_context
    # inside their closure, within the same task that set it.
    @property
    def current_context(self) -> dict[str, str]:
        """Return the execution context of the current task."""
        return _EXECUTION_CONTEXT.get() or {}

    def set_context(self, context: dict[str, str]) -> None:
        """Set the per-call execution context for the current task."""
        _EXECUTION_CONTEXT.set(dict(context))

    def clear_context(self) -> None:
        """Clear the per-call execution context of the current task."""
        _EXECUTION_CONTEXT.set({})

    def get_workspace_actions(self) -> list[dict]:
        """Return a copy of collected workspace actions."""
        return list(self._workspace_actions)

    def clear_workspace_actions(self) -> None:
        """Clear the workspace actions buffer."""
        self._workspace_actions.clear()

    async def flush(self) -> None:
        """Finish all durable rate and audit writes before process shutdown."""
        await self.persistent_limiter.flush()
        if self._audit_tasks:
            await asyncio.gather(*tuple(self._audit_tasks))

    def scheduler_authorizations(self, names: list[str]) -> list[dict[str, str]]:
        """Validate scheduler selections and pin their permission revisions."""
        if not self._finalized:
            raise ToolBrokerError("Tool broker is not finalized")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                raise ToolBrokerError(f"Duplicate scheduled tool: {name}")
            definition = self.registry.get(name)
            if definition is None:
                raise ToolBrokerError(f"Unknown scheduled tool: {name}")
            seen.add(name)
            entries.append(
                {
                    "tool_name": name,
                    "approved_policy_revision": definition.permission.revision,
                }
            )
        return entries

    def _record_workspace_action(
        self,
        tool_name: str,
        parameters: dict,
        result: object,
        success: bool,
    ) -> None:
        """Record a workspace-relevant tool call if applicable."""
        if tool_name not in WORKSPACE_TOOL_NAMES:
            return
        from backend.security.content_filter import filter_output

        filtered = filter_output(json.dumps(_bounded_value(parameters))).text
        try:
            safe_parameters = json.loads(filtered)
        except TypeError, ValueError:
            safe_parameters = {"redacted": True}
        action: dict = {
            "tool": tool_name,
            "params": safe_parameters,
            "success": success,
            "timestamp": time.time(),
        }
        if not success and result is not None:
            action["error"] = str(result)[:200]
        self._workspace_actions.append(action)

    def _log_audit(
        self,
        event_type: str,
        tool_name: str,
        parameters: object = None,
        result: object = None,
        duration_ms: float | None = None,
        authorization: AuthorizationContext | None = None,
        correlation: Mapping[str, str] | None = None,
    ) -> None:
        """Write an audit log entry when an audit callback is available.

        Redacts credentials from parameters and truncates
        the result to 200 characters before logging.

        Args:
            event_type: Audit event type.
            tool_name: Name of the tool.
            parameters: Raw parameters (will be redacted).
            result: Execution result (will be truncated).
            duration_ms: Execution time in milliseconds.
            authorization: Authorization decision metadata.
            correlation: Immutable execution identity for this outcome.
        """
        from backend.security.content_filter import (
            filter_output,
        )

        audit_payload: dict[str, object] = {
            "arguments": _bounded_value(parameters),
            "authorization": (
                {
                    "permission_mode": authorization.permission_mode,
                    "policy_revision": authorization.policy_revision,
                    "source": authorization.source,
                    "authorization_id": authorization.authorization_id,
                }
                if authorization is not None
                else None
            ),
            "correlation": {
                key: (correlation or {}).get(key, "")
                for key in (
                    "agent_run_id",
                    "invocation_id",
                    "conversation_id",
                    "session_id",
                    "scheduled_task_id",
                )
            },
        }
        redacted_params = None
        if parameters is not None or authorization is not None:
            raw = json.dumps(audit_payload)
            redacted_params = filter_output(raw).text

        result_summary = None
        if result is not None:
            result_summary = filter_output(str(result)).text[:200]

        if self.audit_fn is None:
            return

        audit_task = asyncio.ensure_future(
            self.audit_fn(
                event_type,
                tool_name,
                parameters=redacted_params,
                result=result_summary,
                duration_ms=duration_ms,
            )
        )
        self._audit_tasks.add(audit_task)
        audit_task.add_done_callback(self._audit_tasks.discard)

    def bind_handler(self, tool_name: str, handler: Callable) -> None:
        """Bind one handler before finalization without allowing replacement.

        Args:
            tool_name: Tool name.
            handler: Callable to execute the tool.
        """
        if self._finalized:
            raise RuntimeError("Tool broker is finalized")
        if tool_name in self._handlers:
            raise ValueError(f"Tool handler already bound: {tool_name}")
        self._handlers[tool_name] = handler

    def record_authorization_outcome(
        self,
        *,
        prepared: PreparedInvocation,
        authorization: AuthorizationContext,
        outcome: str,
        reason: str,
        correlation: Mapping[str, str],
    ) -> None:
        """Persist one non-dispatch authorization outcome with stable identity."""
        if outcome not in {"tool_blocked", "tool_denied"}:
            raise ValueError(f"Unsupported authorization outcome: {outcome}")
        self._log_audit(
            outcome,
            prepared.tool_name,
            dict(prepared.canonical_parameters),
            reason,
            duration_ms=0.0,
            authorization=authorization,
            correlation=correlation,
        )

    def finalize(self) -> None:
        """Validate and freeze the definition and handler snapshots atomically."""
        if self._finalized:
            return
        definitions = set(self.registry.tool_names)
        handlers = set(self._handlers)
        missing_handlers = sorted(definitions - handlers)
        unknown_handlers = sorted(handlers - definitions)
        if missing_handlers or unknown_handlers:
            details: list[str] = []
            if missing_handlers:
                details.append("missing handlers: " + ", ".join(missing_handlers))
            if unknown_handlers:
                details.append("unknown handlers: " + ", ".join(unknown_handlers))
            raise RuntimeError("Invalid tool runtime: " + "; ".join(details))
        self.registry.freeze()
        self._handlers = MappingProxyType(dict(self._handlers))
        self._finalized = True

    @property
    def finalized(self) -> bool:
        """Whether both runtime snapshots are immutable."""
        return self._finalized

    def list_catalog(self) -> list[dict[str, str]]:
        """Return the finalized scheduler/admin projection."""
        if not self._finalized:
            raise RuntimeError("Tool broker is not finalized")
        return self.registry.list_catalog()

    def workspace_roots(self) -> list[str]:
        """Return current canonical workspace roots for policy prerequisites."""
        if self.workspace_roots_fn is None:
            return []
        return list(self.workspace_roots_fn())

    def _tool_definition(self, tool_name: str) -> ToolDefinition:
        """Return one registered tool definition or raise a broker error."""
        tool_def = self.registry.get(tool_name)
        if tool_def is None:
            raise ToolBrokerError(f"Unknown tool: {tool_name}")
        return tool_def

    def _ensure_rate_limits(self, tool_name: str) -> None:
        """Reject one tool call when any rate limit is exceeded."""
        if not self.rate_limiter.is_allowed(tool_name):
            raise ToolBrokerError(f"Rate limit exceeded for {tool_name}")
        if not self.persistent_limiter.is_allowed(tool_name):
            raise ToolBrokerError(f"Rate limit exceeded for {tool_name}")

    def _handler(self, tool_name: str) -> Callable:
        """Return one registered execution handler or raise a broker error."""
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ToolBrokerError(f"No handler for tool: {tool_name}")
        return handler

    def _record_tool_usage(self, tool_name: str) -> None:
        """Record one successful rate-limit hit for the selected tool."""
        self.rate_limiter.record(tool_name)
        self.persistent_limiter.record(tool_name)

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        """Return elapsed milliseconds since one monotonic timestamp."""
        return (time.monotonic() - start) * 1000

    def prepare(self, tool_name: str, parameters: dict) -> PreparedInvocation:
        """Validate raw model arguments once and return canonical typed values."""
        if not self._finalized:
            raise ToolBrokerError("Tool broker is not finalized")
        try:
            tool_def = self._tool_definition(tool_name)
        except ToolBrokerError:
            self._log_audit(
                "tool_rejected",
                tool_name,
                parameters=parameters,
                result="unknown tool",
                correlation=self.current_context,
            )
            raise
        try:
            validated = tool_def.input_model.model_validate(parameters)
        except PydanticError as exc:
            raise ToolBrokerError(f"Validation failed: {exc}") from exc
        canonical_parameters = validated.model_dump(mode="json")
        path_argument = (
            tool_def.permission.canonical_path_argument
            or tool_def.permission.workspace_argument
        )
        if path_argument:
            raw_workspace = str(canonical_parameters.get(path_argument, "")).strip()
            if not raw_workspace:
                mode = tool_def.permission.resolve(validated)
                if not (tool_def.permission.action_modes and mode.value == "implicit"):
                    # Tool schemas document the path as optional ("defaults to
                    # workspace root"); honour that here so the canonical hash,
                    # authorization scope, and workspace revalidation all see
                    # the real directory.
                    roots = [
                        str(root)
                        for root in self.workspace_roots()
                        if str(root).strip()
                    ]
                    if not roots:
                        raise ToolBrokerError(
                            f"{path_argument} is empty and no workspace root "
                            "is configured"
                        )
                    canonical_parameters[path_argument] = str(
                        Path(roots[0]).expanduser().resolve(strict=False)
                    )
            else:
                canonical_parameters[path_argument] = str(
                    Path(raw_workspace).expanduser().resolve(strict=False)
                )
        canonical_json = json.dumps(
            canonical_parameters,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        frozen_parameters = _freeze_json(canonical_parameters)
        if not isinstance(frozen_parameters, MappingProxyType):
            raise ToolBrokerError("Canonical tool input must be an object")
        immutable_input = validated.model_copy(
            update={path_argument: canonical_parameters[path_argument]}
            if path_argument and canonical_parameters.get(path_argument)
            else {},
        )
        return PreparedInvocation(
            definition=tool_def,
            typed_input=immutable_input,
            canonical_parameters=frozen_parameters,
            canonical_json=canonical_json,
            canonical_hash=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        )

    def restore_prepared(
        self,
        tool_name: str,
        canonical_parameters: dict,
        *,
        expected_hash: str,
        expected_policy_revision: str,
    ) -> PreparedInvocation:
        """Restore and verify the immutable snapshot persisted before a pause."""
        prepared = self.prepare(tool_name, canonical_parameters)
        if prepared.canonical_hash != expected_hash:
            raise ToolBrokerError("Persisted invocation hash mismatch")
        if prepared.definition.permission.revision != expected_policy_revision:
            raise ToolBrokerError("Persisted invocation policy revision mismatch")
        return prepared

    def list_schemas(self) -> list[dict]:
        """List all tool schemas for LLM context.

        Returns:
            List of tool schema dicts.
        """
        return self.registry.list_schemas()

    def list_openai_schemas(
        self,
        allowed_names: set[str] | None = None,
    ) -> list[dict]:
        """List all tool schemas in OpenAI function calling format.

        Returns:
            List of OpenAI-format tool dicts.
        """
        return self.registry.list_openai_schemas(allowed_names=allowed_names)

    async def execute_prepared(
        self,
        prepared: PreparedInvocation,
        authorization: AuthorizationContext,
        correlation: Mapping[str, str] | None = None,
    ) -> ToolResult:
        """Dispatch one already validated and authorized invocation."""
        start = time.monotonic()
        tool_name = prepared.tool_name
        parameters = dict(prepared.canonical_parameters)

        try:
            self._revalidate_workspace_target(prepared)
            self._ensure_rate_limits(tool_name)
            handler = self._handler(tool_name)

            self._record_tool_usage(tool_name)
            result = await handler(prepared.typed_input)
            elapsed = self._elapsed_ms(start)

            self._record_workspace_action(tool_name, parameters, result, True)

            self._log_audit(
                "tool_success",
                tool_name,
                parameters,
                str(result),
                duration_ms=elapsed,
                authorization=authorization,
                correlation=correlation,
            )

            return ToolResult(
                tool_name=tool_name,
                success=True,
                result=result,
                authorization=authorization,
                duration_ms=elapsed,
            )

        except Exception as exc:
            elapsed = self._elapsed_ms(start)
            self._record_workspace_action(tool_name, parameters, str(exc), False)
            self._log_audit(
                "tool_rejected" if isinstance(exc, ToolBrokerError) else "tool_error",
                tool_name,
                parameters,
                str(exc),
                duration_ms=elapsed,
                authorization=authorization,
                correlation=correlation,
            )
            raise

    def _revalidate_workspace_target(self, prepared: PreparedInvocation) -> None:
        """Recheck the canonical workspace target immediately before dispatch."""
        argument = prepared.definition.permission.workspace_argument
        if not argument:
            return
        target = Path(str(prepared.canonical_parameters.get(argument, "")))
        resolved = target.expanduser().resolve(strict=False)
        if str(resolved) != str(target):
            raise ToolBrokerError("Workspace target changed after authorization")
        allowed = False
        for raw_root in self.workspace_roots():
            root = Path(raw_root).expanduser().resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            allowed = True
            break
        if not allowed:
            raise ToolBrokerError("Workspace target is no longer authorized")

    def reset_rate_limit(self, tool_name: str) -> None:
        """Reset the rate limit for a specific tool.

        Args:
            tool_name: Tool whose limit to reset.
        """
        self.rate_limiter.reset_tool(tool_name)
