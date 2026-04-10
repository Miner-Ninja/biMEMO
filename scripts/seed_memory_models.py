"""Small data models for the full-cycle memory test harness."""

from __future__ import annotations


class TestResult:
    """Single test outcome."""

    def __init__(self, name: str, query: str) -> None:
        self.name = name
        self.query = query
        self.passed: bool = False
        self.details: list[str] = []
        self.response_text: str = ""
        self.retrieved_memories: list[dict] | None = None
        self.retrieved_messages: list[dict] | None = None
        self.elapsed: float = 0.0
        self.error: str | None = None
