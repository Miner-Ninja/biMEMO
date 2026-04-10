"""Tool registry for built-in tool management.

Maintains a registry of tools with their schemas, trust
zones, and worker routing. Provides LLM-compatible tool
discovery.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel

from backend.trust.zones import TrustZone


logger = logging.getLogger(__name__)


class WorkerTarget(StrEnum):
    """Target worker for tool execution.

    CORE: Execute in the Core process.
    FS_WORKER: Route to filesystem worker.
    NET_WORKER: Route to network worker.
    """

    CORE = "core"
    FS_WORKER = "fs_worker"
    NET_WORKER = "net_worker"


@dataclass(frozen=True)
class ToolDefinition:
    """Definition of a registered tool.

    Attributes:
        name: Unique tool name.
        description: Human-readable description.
        input_model: Pydantic model for input validation.
        zone: Trust zone classification.
        worker: Target worker for execution.
    """

    name: str
    description: str
    input_model: type[BaseModel]
    zone: TrustZone
    worker: WorkerTarget


@dataclass
class ToolRegistry:
    """Registry of available tools.

    Tools are registered at startup and cannot be
    modified at runtime.
    """

    _tools: dict[str, ToolDefinition] = field(default_factory=dict)

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition.

        Args:
            tool: Tool definition to register.

        Raises:
            ValueError: If tool name already registered.
        """
        if tool.name in self._tools:
            msg = f"Tool already registered: {tool.name}"
            raise ValueError(msg)
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    @staticmethod
    def _schema_entry(tool: ToolDefinition, schema: dict) -> dict:
        """Build one LLM-facing schema entry."""
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": schema,
        }

    @staticmethod
    def _openai_tool_entry(tool: ToolDefinition, schema: dict) -> dict:
        """Build one OpenAI tool entry."""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
            },
        }

    @staticmethod
    def _tool_schema(tool: ToolDefinition) -> dict:
        """Return one tool's raw JSON schema."""
        return tool.input_model.model_json_schema()

    @staticmethod
    def _iter_tools(
        tools: dict[str, ToolDefinition],
        allowed_names: set[str] | None = None,
    ) -> list[ToolDefinition]:
        """Return registered tools filtered by an optional allowlist."""
        result: list[ToolDefinition] = []
        for tool in tools.values():
            if allowed_names is not None and tool.name not in allowed_names:
                continue
            result.append(tool)
        return result

    @staticmethod
    def _clean_openai_schema(schema: dict) -> dict:
        """Normalize one schema for OpenAI function calling."""
        _strip_schema_titles(schema)
        schema.setdefault("additionalProperties", False)
        return schema

    def get(self, name: str) -> ToolDefinition | None:
        """Look up a tool by name.

        Args:
            name: Tool name.

        Returns:
            ToolDefinition if found, None otherwise.
        """
        return self._tools.get(name)

    def list_schemas(self) -> list[dict]:
        """Generate LLM-compatible tool schemas.

        Returns:
            List of tool schema dicts for LLM context.
        """
        return [
            self._schema_entry(tool, self._tool_schema(tool))
            for tool in self._iter_tools(self._tools)
        ]

    def list_openai_schemas(
        self,
        allowed_names: set[str] | None = None,
    ) -> list[dict]:
        """Generate OpenAI-compatible tool schemas for native function calling.

        Converts Pydantic JSON schemas to the OpenAI tools format,
        stripping internal ``title`` fields and ensuring
        ``additionalProperties: false`` is set.

        Returns:
            List of OpenAI-format tool dicts.
        """
        result = []
        for tool in self._iter_tools(self._tools, allowed_names):
            result.append(self._openai_schema_entry(tool))
        return result

    def _openai_schema_entry(self, tool: ToolDefinition) -> dict:
        """Build one OpenAI tool schema entry from a registered tool."""
        schema = self._clean_openai_schema(self._tool_schema(tool))
        return self._openai_tool_entry(tool, schema)

    @property
    def tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())


def _strip_schema_titles(schema: dict) -> None:
    """Recursively remove ``title`` keys from a JSON schema.

    OpenAI function calling does not use Pydantic-generated
    ``title`` fields; removing them keeps payloads clean.
    """
    schema.pop("title", None)
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for property_schema in value.values():
                if isinstance(property_schema, dict):
                    _strip_schema_titles(property_schema)
            continue
        if isinstance(value, dict):
            _strip_schema_titles(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strip_schema_titles(item)
