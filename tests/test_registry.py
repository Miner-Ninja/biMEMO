"""Tests for tool registry.

Covers registration, lookup, schema generation,
and duplicate detection.
"""

import pytest
from backend.tools.builtin import SkillCreateInput
from backend.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    WorkerTarget,
)
from backend.trust.zones import TrustZone
from pydantic import BaseModel


class _DummyInput(BaseModel, extra="forbid"):
    """Dummy input model for tests."""

    text: str


def _make_tool(name: str = "test_tool"):
    """Create a test tool definition."""
    return ToolDefinition(
        name=name,
        description="A test tool",
        input_model=_DummyInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    )


class TestRegister:
    """Tests for tool registration."""

    def test_register_and_get(self):
        """Verify tool can be registered and retrieved."""
        tool_registry = ToolRegistry()
        tool = _make_tool()
        tool_registry.register(tool)
        assert tool_registry.get("test_tool") is tool

    def test_duplicate_raises(self):
        """Verify duplicate registration raises."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool())
        with pytest.raises(ValueError, match="already"):
            tool_registry.register(_make_tool())

    def test_get_unknown_returns_none(self):
        """Verify unknown tool returns None."""
        tool_registry = ToolRegistry()
        assert tool_registry.get("nonexistent") is None


class TestListSchemas:
    """Tests for LLM schema generation."""

    def test_schemas_generated(self):
        """Verify schemas contain tool info."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool("tool_a"))
        tool_registry.register(_make_tool("tool_b"))
        schemas = tool_registry.list_schemas()
        assert len(schemas) == 2
        names = {s["name"] for s in schemas}
        assert names == {"tool_a", "tool_b"}

    def test_schema_has_parameters(self):
        """Verify schema includes parameter definitions."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool())
        schemas = tool_registry.list_schemas()
        assert "parameters" in schemas[0]
        props = schemas[0]["parameters"]["properties"]
        assert "text" in props

    def test_openai_schema_preserves_property_named_title(self):
        """Verify schema cleanup keeps real ``title`` parameter names."""
        tool_registry = ToolRegistry()
        tool_registry.register(
            ToolDefinition(
                name="skill_create",
                description="Create skill",
                input_model=SkillCreateInput,
                zone=TrustZone.GREEN,
                worker=WorkerTarget.CORE,
            )
        )

        schemas = tool_registry.list_openai_schemas()
        properties = schemas[0]["function"]["parameters"]["properties"]

        assert "title" in properties
        assert properties["title"]["type"] == "string"


class TestToolNames:
    """Tests for tool name listing."""

    def test_lists_names(self):
        """Verify all names are listed."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool("a"))
        tool_registry.register(_make_tool("b"))
        assert set(tool_registry.tool_names) == {"a", "b"}

    def test_empty_registry(self):
        """Verify empty registry returns empty list."""
        tool_registry = ToolRegistry()
        assert tool_registry.tool_names == []
