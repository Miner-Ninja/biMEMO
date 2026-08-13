"""Chat usage statistics models shared across transport boundaries."""

from pydantic import BaseModel, ConfigDict


class StrictChatStatsModel(BaseModel):
    """Base model for strict chat statistics payloads."""

    model_config = ConfigDict(extra="forbid")


class ContextUsage(StrictChatStatsModel):
    """Context window usage statistics for a chat response."""

    total: int
    used: int
    free: int
    used_percent: float


class StageTiming(StrictChatStatsModel):
    """One labelled processing-phase duration for a chat response."""

    label: str
    duration_ms: float


class TimingStats(StrictChatStatsModel):
    """Timing statistics for a chat response."""

    prompt_ms: float
    generation_ms: float
    total_ms: float
    stages: list[StageTiming] = []


class ZoneBudgetItem(StrictChatStatsModel):
    """Token budget and usage for one context zone."""

    budget: int
    used: int = 0
    label: str


class MemoryZones(StrictChatStatsModel):
    """Memory zone budget breakdown for a chat response."""

    working_memory: ZoneBudgetItem
    recall_results: ZoneBudgetItem
    conversation_summary: ZoneBudgetItem
    total_available: int


class StageUsage(StrictChatStatsModel):
    """Token and timing summary for one processing stage."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    prompt_ms: float
    generation_ms: float


class ExtendedUsage(StrictChatStatsModel):
    """Extended token and timing usage for a chat response."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    context: ContextUsage | None = None
    memory_zones: MemoryZones | None = None
    timing: TimingStats
    model: str
    iterations: int
    stage1: StageUsage | None = None
    stage2: StageUsage | None = None
    tool_capable: bool = False
    tool_used: bool = False
