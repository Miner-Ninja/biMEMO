"""Built-in tool Pydantic input models.

Defines input schemas for all built-in tools. Each model
is used for parameter validation before execution.
"""

from pydantic import BaseModel, ConfigDict, Field


class StrictToolInput(BaseModel):
    """Base model for strict tool inputs."""

    model_config = ConfigDict(extra="forbid")


class FileReadInput(StrictToolInput):
    """Input for file_read tool.

    Attributes:
        path: File path to read.
    """

    path: str = Field(description="Path to file to read")


class FileWriteInput(StrictToolInput):
    """Input for file_write tool.

    Attributes:
        path: File path to write.
        content: Content to write.
    """

    path: str = Field(description="Path to write to")
    content: str = Field(description="Content to write")


class FileSearchInput(StrictToolInput):
    """Input for file_search tool.

    Attributes:
        directory: Directory to search in.
        pattern: Glob pattern to match.
    """

    directory: str = Field(description="Directory to search")
    pattern: str = Field(description="File pattern (glob)")


class WebSearchInput(StrictToolInput):
    """Input for web_search tool.

    Attributes:
        query: Search query (max 200 chars).
    """

    query: str = Field(
        max_length=200,
        description="Search query",
    )


class MemoryRecallInput(StrictToolInput):
    """Input for memory_recall tool.

    Attributes:
        query: Natural language query to search past conversations and memories.
    """

    query: str = Field(
        max_length=300,
        description="What to recall from past conversations",
    )


class HttpFetchInput(StrictToolInput):
    """Input for http_fetch tool.

    Attributes:
        url: URL to fetch (HTTPS only).
    """

    url: str = Field(description="URL to fetch")


class EmailSendInput(StrictToolInput):
    """Input for email_send tool.

    Attributes:
        to: Recipient email address.
        subject: Email subject.
        body: Email body text.
    """

    to: str = Field(description="Recipient email")
    subject: str = Field(description="Email subject")
    body: str = Field(description="Email body")


class ScheduleTaskInput(StrictToolInput):
    """Input for schedule_task tool.

    Attributes:
        action: Operation to perform.
        name: Task name (required for create).
        prompt: Task prompt (required for create).
        schedule: Schedule expression (required for create).
        task_type: Type of task (for create).
        allowed_tools: JSON list of approved tool names (for create agent_task).
        task_id: Task ID (required for pause/resume/remove).
        conversation_id: Optional originating conversation id, used to
            deliver task results back via SSE and chat bridge.
    """

    action: str = Field(description="Action: create, list, pause, resume, remove")
    name: str = Field(default="", description="Task name (for create)")
    prompt: str = Field(default="", description="Task prompt (for create)")
    schedule: str = Field(
        default="",
        description=(
            "Schedule: ISO timestamp (2026-04-10T09:00:00), "
            "interval (30m, 2h, 1d), or cron (0 9 * * *)"
        ),
    )
    task_type: str = Field(
        default="agent_task",
        description="Task type: reminder or agent_task",
    )
    allowed_tools: str = Field(
        default="[]",
        description="JSON list of tool names approved for this task",
    )
    task_id: str = Field(default="", description="Task ID (for pause/resume/remove)")
    conversation_id: str = Field(
        default="",
        description=(
            "Optional source conversation id; if set, results will be "
            "delivered to that chat (and any linked messenger bridge)"
        ),
    )


class SkillCreateInput(StrictToolInput):
    """Input for skill_create tool.

    Attributes:
        name: Kebab-case unique identifier for the skill.
        title: Human-readable skill name.
        description: One-line summary of what the skill does.
        trigger: When to use this skill.
        procedure: JSON array of procedure steps.
        errors: JSON array of error/fix pairs.
        verification: How to verify the result.
        tool_sequence: Comma-separated list of tools used.
    """

    name: str = Field(
        description="Kebab-case skill identifier (e.g. crypto-price-report)"
    )
    title: str = Field(description="Human-readable skill name")
    description: str = Field(description="One-line summary of the skill")
    trigger: str = Field(default="", description="When to use this skill")
    procedure: str = Field(
        default="[]", description="JSON array of {tool, query, note} steps"
    )
    errors: str = Field(default="[]", description="JSON array of {name, fix} pairs")
    verification: str = Field(
        default="", description="How to verify the result is correct"
    )
    tool_sequence: str = Field(
        default="",
        description="Comma-separated tools used (e.g. web_search,email_send)",
    )


class SkillSearchInput(StrictToolInput):
    """Input for skill_search tool.

    Attributes:
        query: Search query to find relevant skills.
    """

    query: str = Field(description="Search query to find relevant skills")


class SkillLoadInput(StrictToolInput):
    """Input for skill_load tool.

    Attributes:
        name: Name of the skill to load.
    """

    name: str = Field(description="Skill name to load (from matched_skills list)")


class SystemStatusInput(StrictToolInput):
    """Input for system_status tool (no parameters)."""
