"""Immutable registry for built-in tool schemas and permission policies."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel


logger = logging.getLogger(__name__)

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


class PermissionMode(StrEnum):
    """Resolved permission behavior for one typed invocation."""

    IMPLICIT = "implicit"
    CONVERSATION_CAPABILITY = "conversation_capability"
    SCOPED = "scoped"
    PER_INVOCATION = "per_invocation"
    BLOCKED = "blocked"


class PermissionScope(StrEnum):
    """Backend-owned canonical scope strategy."""

    NONE = "none"
    CONVERSATION_CAPABILITY = "conversation_capability"
    WORKSPACE_ROOT = "workspace_root"
    INVOCATION = "invocation"


class PermissionReply(StrEnum):
    """How a user decision may be accepted."""

    NONE = "none"
    LLM_CLASSIFIED = "llm_classified"
    EXPLICIT_ACTION_ID = "explicit_action_id"


@dataclass(frozen=True)
class PermissionPolicy:
    """Versioned declarative permission policy owned by backend code."""

    mode: PermissionMode | None
    revision: str = "registry-v1"
    scope: PermissionScope = PermissionScope.NONE
    reply: PermissionReply = PermissionReply.NONE
    scope_argument: str = ""
    workspace_argument: str = ""
    canonical_path_argument: str = ""
    scheduler_preauthorizable: bool = True
    action_argument: str = ""
    action_modes: tuple[tuple[str, PermissionMode], ...] = ()

    def resolve(self, typed_input: BaseModel) -> PermissionMode:
        """Resolve a static or action-dependent mode from typed arguments."""
        if self.action_modes:
            action = str(getattr(typed_input, self.action_argument, ""))
            modes = dict(self.action_modes)
            try:
                return modes[action]
            except KeyError as exc:
                raise ValueError(f"Unsupported policy action: {action!r}") from exc
        if self.mode is None:
            raise ValueError("Static permission policy has no mode")
        return self.mode

    def validate(self, input_model: type[BaseModel]) -> None:
        """Reject incomplete or internally inconsistent policy declarations."""
        if not self.revision.strip():
            raise ValueError("Permission policy revision is required")
        fields = input_model.model_fields
        if self.action_modes:
            if self.mode is not None:
                raise ValueError("Action-dependent policy cannot also have a mode")
            if not self.action_argument or self.action_argument not in fields:
                raise ValueError("Action-dependent policy has no valid action argument")
            actions = [name for name, _mode in self.action_modes]
            if len(actions) != len(set(actions)):
                raise ValueError("Permission policy contains duplicate actions")
        elif self.mode is None:
            raise ValueError("Permission policy mode is required")
        if self.scope is PermissionScope.WORKSPACE_ROOT and (
            not self.scope_argument or self.scope_argument not in fields
        ):
            raise ValueError("Workspace policy has no valid scope argument")
        if self.workspace_argument and self.workspace_argument not in fields:
            raise ValueError("Permission policy has no valid workspace argument")
        if self.canonical_path_argument and self.canonical_path_argument not in fields:
            raise ValueError("Permission policy has no valid canonical path argument")
        if self.reply is PermissionReply.LLM_CLASSIFIED:
            reusable = {
                PermissionMode.CONVERSATION_CAPABILITY,
                PermissionMode.SCOPED,
            }
            modes = {mode for _action, mode in self.action_modes}
            if self.mode is not None:
                modes.add(self.mode)
            if not modes or not modes.issubset(reusable):
                raise ValueError("LLM-classified replies require reusable modes")


@dataclass(frozen=True)
class ToolDefinition:
    """One immutable built-in tool definition."""

    name: str
    description: str
    input_model: type[BaseModel]
    permission: PermissionPolicy


@dataclass(frozen=True)
class ToolRegistry:
    """Single authority for tool schemas and backend permission policy."""

    _tools: Mapping[str, ToolDefinition] = field(default_factory=dict)
    _frozen: bool = False

    def register(self, tool: ToolDefinition) -> None:
        """Register one definition before finalization."""
        if self._frozen:
            raise RuntimeError("Tool registry is frozen")
        if not _TOOL_NAME_RE.fullmatch(tool.name):
            raise ValueError(f"Invalid tool name: {tool.name}")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        if not isinstance(self._tools, dict):
            raise RuntimeError("Tool registry storage is immutable")
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def validate(self) -> None:
        """Validate every schema and policy without mutating the registry."""
        errors: list[str] = []
        for tool in self._tools.values():
            try:
                schema = tool.input_model.model_json_schema()
                if schema.get("type") != "object":
                    raise ValueError("input schema root must be an object")
                if not tool.input_model.model_config.get("frozen", False):
                    raise ValueError("input model must be frozen")
                tool.permission.validate(tool.input_model)
            except (TypeError, ValueError) as exc:
                errors.append(f"{tool.name}: {exc}")
        if errors:
            raise RuntimeError("Invalid tool registry: " + "; ".join(errors))

    def freeze(self) -> None:
        """Validate and prevent later definition mutation."""
        self.validate()
        object.__setattr__(self, "_tools", MappingProxyType(dict(self._tools)))
        object.__setattr__(self, "_frozen", True)

    @property
    def frozen(self) -> bool:
        """Whether startup finalized the definition snapshot."""
        return self._frozen

    @property
    def definitions(self) -> Mapping[str, ToolDefinition]:
        """Return a read-only definition mapping."""
        return self._tools

    def get(self, name: str) -> ToolDefinition | None:
        """Look up one definition."""
        return self._tools.get(name)

    @staticmethod
    def _tool_schema(tool: ToolDefinition) -> dict:
        return tool.input_model.model_json_schema()

    @staticmethod
    def _iter_tools(
        tools: dict[str, ToolDefinition],
        allowed_names: set[str] | None = None,
    ) -> list[ToolDefinition]:
        return [
            tool
            for tool in tools.values()
            if allowed_names is None or tool.name in allowed_names
        ]

    def list_schemas(self) -> list[dict]:
        """Return the minimal generic LLM projection."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": self._tool_schema(tool),
            }
            for tool in self._iter_tools(self._tools)
        ]

    def list_openai_schemas(
        self,
        allowed_names: set[str] | None = None,
    ) -> list[dict]:
        """Return the minimal OpenAI native-tool projection."""
        result: list[dict] = []
        for tool in self._iter_tools(self._tools, allowed_names):
            schema = self._tool_schema(tool)
            _strip_schema_titles(schema)
            schema.setdefault("additionalProperties", False)
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": schema,
                    },
                }
            )
        return result

    def list_catalog(self) -> list[dict[str, str]]:
        """Return the minimal admin/scheduler projection."""
        if not self._frozen:
            raise RuntimeError("Tool registry is not finalized")
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]

    @property
    def tool_names(self) -> list[str]:
        """Return registered names in stable registration order."""
        return list(self._tools)


def _strip_schema_titles(schema: dict) -> None:
    """Remove schema annotation titles without deleting a property named title."""
    schema.pop("title", None)
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for property_schema in value.values():
                if isinstance(property_schema, dict):
                    _strip_schema_titles(property_schema)
        elif isinstance(value, dict):
            _strip_schema_titles(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strip_schema_titles(item)
