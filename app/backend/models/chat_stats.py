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


class TimingStats(StrictChatStatsModel):
    """Timing statistics for a chat response."""

    prompt_ms: float
    generation_ms: float
    total_ms: float


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
