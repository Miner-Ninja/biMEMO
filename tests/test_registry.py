"""Tests for tool registry.

Covers registration, lookup, schema generation,
and duplicate detection.
"""

from unittest.mock import AsyncMock

import pytest
from backend.tools.broker import ToolBroker
from backend.tools.builtin import CodeSearchInput, SkillCreateInput, WorkspaceNoteInput
from backend.tools.handlers.registration import TOOL_DEFINITIONS
from backend.tools.registry import (
    PermissionMode,
    PermissionPolicy,
    ToolDefinition,
    ToolRegistry,
)
from backend.workers.fs_worker import WorkspaceDirs
from pydantic import BaseModel, ValidationError


class _DummyInput(BaseModel, extra="forbid", frozen=True):
    """Dummy input model for tests."""

    text: str


def test_code_search_input_enforces_resource_bounds() -> None:
    """The public tool schema bounds output and context amplification."""
    assert CodeSearchInput(pattern="x", max_results=1, context_lines=0)
    with pytest.raises(ValidationError):
        CodeSearchInput(pattern="x", max_results=201)
    with pytest.raises(ValidationError):
        CodeSearchInput(pattern="x", context_lines=11)


def _make_tool(name: str = "test_tool"):
    """Create a test tool definition."""
    return ToolDefinition(
        name=name,
        description="A test tool",
        input_model=_DummyInput,
        permission=PermissionPolicy(mode=PermissionMode.IMPLICIT),
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
                permission=PermissionPolicy(mode=PermissionMode.IMPLICIT),
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


class TestFreeze:
    """Tests for immutable finalized registry behavior."""

    def test_frozen_registry_rejects_registration(self):
        """A finalized registry rejects late definitions."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool())
        tool_registry.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            tool_registry.register(_make_tool("other"))

    def test_catalog_requires_frozen_registry(self):
        """Catalog projection is unavailable before validation and freeze."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool())
        with pytest.raises(RuntimeError, match="not finalized"):
            tool_registry.list_catalog()

    def test_catalog_does_not_expose_permission_policy(self):
        """Administrative catalog contains presentation fields only."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool())
        tool_registry.freeze()

        assert tool_registry.list_catalog() == [
            {"name": "test_tool", "description": "A test tool"}
        ]

    def test_frozen_registry_mapping_cannot_be_mutated(self):
        """Finalization freezes the backing definition map itself."""
        tool_registry = ToolRegistry()
        tool_registry.register(_make_tool())
        tool_registry.freeze()

        with pytest.raises(TypeError):
            tool_registry.definitions["other"] = _make_tool("other")


def test_set_workspace_symlink_is_canonicalized_before_policy(tmp_path) -> None:
    """Workspace policy and execution receive the same canonical path."""
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    definition = next(item for item in TOOL_DEFINITIONS if item.name == "set_workspace")
    registry = ToolRegistry()
    registry.register(definition)
    broker = ToolBroker(registry)
    broker.bind_handler("set_workspace", AsyncMock())
    broker.finalize()

    prepared = broker.prepare(
        "set_workspace",
        {"action": "add", "path": str(link)},
    )

    assert prepared.canonical_parameters["path"] == str(target.resolve())
    assert prepared.typed_input.path == str(target.resolve())


def test_set_workspace_list_accepts_empty_path() -> None:
    """Listing roots does not invent a workspace containment requirement."""
    definition = next(item for item in TOOL_DEFINITIONS if item.name == "set_workspace")
    registry = ToolRegistry()
    registry.register(definition)
    broker = ToolBroker(registry)
    broker.bind_handler("set_workspace", AsyncMock())
    broker.finalize()

    prepared = broker.prepare("set_workspace", {"action": "list"})

    assert prepared.canonical_parameters["path"] == ""


def test_set_workspace_add_does_not_require_an_existing_root(tmp_path) -> None:
    """A new root is canonicalized without requiring prior authorization."""
    definition = next(item for item in TOOL_DEFINITIONS if item.name == "set_workspace")
    registry = ToolRegistry()
    registry.register(definition)
    broker = ToolBroker(registry, workspace_roots_fn=lambda: [])
    broker.bind_handler("set_workspace", AsyncMock())
    broker.finalize()

    prepared = broker.prepare(
        "set_workspace",
        {"action": "add", "path": str(tmp_path)},
    )

    assert prepared.canonical_parameters["path"] == str(tmp_path.resolve())


async def test_workspace_mutation_updates_runtime_and_policy_roots(tmp_path) -> None:
    """A durable workspace grant is mirrored into both runtime root views."""
    from backend.tools.handlers.registration import _make_set_workspace_handler

    runtime_roots = WorkspaceDirs([str(tmp_path / "internal")])
    policy_roots = WorkspaceDirs([])
    db_client = AsyncMock()
    db_client.list_project_roots.return_value = []
    handler = _make_set_workspace_handler(
        runtime_roots,
        db_client,
        policy_roots,
    )

    await handler(type("Input", (), {"action": "add", "path": str(tmp_path)})())

    assert str(tmp_path.resolve()) in runtime_roots.get()
    assert policy_roots.get() == [str(tmp_path.resolve())]


def test_implicit_persistence_inputs_have_aggregate_byte_limits() -> None:
    """Reject oversized aggregate payloads even when each field is valid."""
    with pytest.raises(ValidationError, match="skill payload exceeds"):
        SkillCreateInput(
            name="bounded",
            title="title",
            description="Ж" * 2_000,
            trigger="Ж" * 4_000,
            procedure="Ж" * 32_000,
            errors="Ж" * 16_000,
            verification="Ж" * 8_000,
            tool_sequence="Ж" * 2_000,
        )

    with pytest.raises(ValidationError, match="workspace note exceeds"):
        WorkspaceNoteInput(
            user_intent="Ж" * 8_000,
            progress="Ж" * 8_000,
            decisions="Ж" * 12_000,
            blockers="Ж" * 12_000,
        )


def test_all_builtin_permission_policies_match_reviewed_matrix() -> None:
    """Pin every built-in tool to one reviewed registry-v1 policy."""
    expected = {
        "file_read": ("scoped", "workspace_root", "llm_classified", "path"),
        "file_write": (
            "per_invocation",
            "invocation",
            "explicit_action_id",
            "path",
        ),
        "file_search": (
            "scoped",
            "workspace_root",
            "llm_classified",
            "directory",
        ),
        "web_search": (
            "conversation_capability",
            "conversation_capability",
            "llm_classified",
            "",
        ),
        "memory_recall": ("implicit", "none", "none", ""),
        "http_fetch": (
            "conversation_capability",
            "conversation_capability",
            "llm_classified",
            "",
        ),
        "email_send": (
            "per_invocation",
            "invocation",
            "explicit_action_id",
            "",
        ),
        "schedule_task": (None, "invocation", "explicit_action_id", ""),
        "system_status": ("implicit", "none", "none", ""),
        "skill_create": ("implicit", "none", "none", ""),
        "skill_search": ("implicit", "none", "none", ""),
        "skill_load": ("implicit", "none", "none", ""),
        "set_workspace": (None, "invocation", "explicit_action_id", ""),
        "workspace_history": (None, "invocation", "explicit_action_id", ""),
        "workspace_note": ("implicit", "none", "none", ""),
        "git_status": ("implicit", "none", "none", "cwd"),
        "git_diff": ("implicit", "none", "none", "cwd"),
        "git_log": ("implicit", "none", "none", "cwd"),
        "git_commit": (
            "per_invocation",
            "invocation",
            "explicit_action_id",
            "cwd",
        ),
        "git_push": (
            "per_invocation",
            "invocation",
            "explicit_action_id",
            "cwd",
        ),
        "code_search": (
            "scoped",
            "workspace_root",
            "llm_classified",
            "cwd",
        ),
        "execute_command": (
            "per_invocation",
            "invocation",
            "explicit_action_id",
            "cwd",
        ),
        "execute_command_stream": (
            "per_invocation",
            "invocation",
            "explicit_action_id",
            "cwd",
        ),
    }
    actual = {
        definition.name: (
            definition.permission.mode.value
            if definition.permission.mode is not None
            else None,
            definition.permission.scope.value,
            definition.permission.reply.value,
            definition.permission.workspace_argument,
        )
        for definition in TOOL_DEFINITIONS
    }
    assert actual == expected
    set_workspace = next(
        definition
        for definition in TOOL_DEFINITIONS
        if definition.name == "set_workspace"
    )
    assert set_workspace.permission.canonical_path_argument == "path"
    assert all(
        definition.permission.revision == "registry-v1"
        for definition in TOOL_DEFINITIONS
    )
    action_policies = {
        definition.name: tuple(
            (action, mode.value) for action, mode in definition.permission.action_modes
        )
        for definition in TOOL_DEFINITIONS
        if definition.permission.action_modes
    }
    assert action_policies == {
        "schedule_task": (
            ("list", "implicit"),
            ("create", "per_invocation"),
            ("pause", "per_invocation"),
            ("resume", "per_invocation"),
            ("remove", "per_invocation"),
        ),
        "set_workspace": (
            ("list", "implicit"),
            ("add", "per_invocation"),
            ("remove", "per_invocation"),
        ),
        "workspace_history": (
            ("get", "implicit"),
            ("clear", "per_invocation"),
        ),
    }
