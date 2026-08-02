"""LLM invocation delegate for the gateway orchestrator.

Handles all LLM calls including SSE streaming, tool choice overrides,
forced web search classification, and the main model/tool loop.
Delegates tool execution to the ToolExecutor and uses helper functions
for LLM capability detection.
"""

from __future__ import annotations

import logging
import re
from ast import literal_eval
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    llm_accepts_sse_queue,
    llm_accepts_tools_override,
    llm_requires_forced_web_search,
    message_may_need_forced_web_search_classifier,
    message_may_need_memory_recall,
    message_requires_forced_web_search,
    request_missing_suggestions,
    request_non_empty_final_answer,
)
from backend.agent.agent_loop_models import ModelLoopResult
from backend.agent.finalization import (
    RequirementSatisfaction,
    ResponseRequirements,
    build_required_tool_failure_answer,
    validate_final_answer,
)
from backend.agent.loop import (
    AgenticLoop,
    LoopState,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.tool_executor import ToolExecutor
    from backend.llm.usage import LLMCallUsage
    from backend.tools.broker import ToolBroker


logger = logging.getLogger(__name__)

MAX_REQUIRED_SEARCH_RESULTS = 10
MAX_REQUIRED_SEARCH_RESULTS_PER_QUERY = 2
MAX_REQUIRED_SEARCH_FIELD_CHARS = 420
MAX_REQUIRED_TOOL_CONTEXT_CHARS = 4000
MAX_REQUIRED_WEB_SEARCH_QUERIES = 4
MIN_GENERIC_TARGET_CHARS = 2

PRICE_SEARCH_MARKERS = frozenset(
    {
        "price",
        "rate",
        "quote",
        "курс",
        "цена",
        "котировка",
        "стоимость",
    }
)

MARKET_TARGET_ALIAS_HINTS = (
    ("Bitcoin", "BTC", ("bitcoin", "btc", "биткоин", "биткойн")),
    ("Ethereum", "ETH", ("ethereum", "eth", "эфириум", "эфир")),
    ("Solana", "SOL", ("solana", "sol", "солана")),
)

CURRENT_DATA_FILLER_PATTERN = re.compile(
    r"\b("
    r"current|latest|today|now|price|prices|rate|rates|quote|quotes|show|find|"
    r"search|look|up|data|for|the|and|or|"
    r"текущий|текущие|актуальный|актуальные|сейчас|сегодня|курс|курсы|цена|"
    r"цены|найди|покажи|посмотри|в|интернете|и|или|на|данный|момент"
    r")\b",
    re.IGNORECASE,
)
TARGET_SPLIT_PATTERN = re.compile(r"\s*(?:,|;|/|\band\b|\bor\b|\bи\b|\bили\b)\s*")
TARGET_LIST_SEPARATOR_PATTERN = re.compile(r"(,|;|/|\band\b|\bor\b|\bи\b|\bили\b)")


@dataclass(frozen=True)
class CurrentDataTarget:
    """One requested current-data target."""

    label: str
    aliases: tuple[str, ...] = ()
    kind: str = "general"


@dataclass(frozen=True)
class CurrentDataEvidencePlan:
    """Search plan for one current-data turn."""

    original_query: str
    targets: tuple[CurrentDataTarget, ...]
    query_plan: tuple[str, ...]
    freshness_required: bool = True


@dataclass(frozen=True)
class CurrentDataTargetEvidence:
    """Evidence status for one current-data target."""

    target: CurrentDataTarget
    found: bool
    sources: tuple[str, ...] = ()
    snippets: tuple[str, ...] = ()
    failure_reason: str = ""


@dataclass(frozen=True)
class CurrentDataEvidenceReport:
    """Evidence coverage report for one current-data turn."""

    plan: CurrentDataEvidencePlan
    target_evidence: tuple[CurrentDataTargetEvidence, ...]

    @property
    def target_labels(self) -> tuple[str, ...]:
        """Return labels for all planned targets."""
        return tuple(evidence.target.label for evidence in self.target_evidence)

    @property
    def missing_labels(self) -> tuple[str, ...]:
        """Return labels for targets without usable evidence."""
        return tuple(
            evidence.target.label
            for evidence in self.target_evidence
            if not evidence.found
        )


class LLMStreamMode(StrEnum):
    """Controls which LLM stream events may reach the UI."""

    ALL = "all"
    THINKING_ONLY = "thinking_only"
    DISABLED = "disabled"


class ThinkingOnlyStreamQueue:
    """Forward only model reasoning events while buffering visible answer text."""

    def __init__(self, target_queue: object | None) -> None:
        """Initialize the queue wrapper."""
        self.target_queue = target_queue

    async def put(self, item: object) -> None:
        """Forward reasoning events to the wrapped queue."""
        if not isinstance(item, dict):
            return
        if item.get("type") != "thinking" or self.target_queue is None:
            return
        await self.target_queue.put(item)


# --- Forced web search classifier prompt ---
FORCED_WEB_SEARCH_CLASSIFIER_PROMPT = (
    "Answer with exactly YES or NO.\n"
    "Return YES when the user's latest message explicitly asks to search the "
    "internet, search the web, look something up online, or when the answer "
    "depends on current external data that should be looked up before "
    "answering.\n"
    "Return NO otherwise.\n"
    "Treat the user's language as arbitrary: the decision must work for any "
    "language.\n"
    "Do not explain your answer."
)


class LLMCaller:
    """Handle LLM invocations with SSE streaming and forced search.

    Attributes:
        model_name: Name of the current LLM model.
        web_search_classifier_fn: Optional web search classifier callable.
        memory_recall_classifier_fn: Optional memory recall classifier callable.
        broker: Tool broker for forced tool execution.
    """

    def __init__(
        self,
        model_name: str,
        web_search_classifier_fn: Callable | None,
        memory_recall_classifier_fn: Callable | None,
        broker: ToolBroker,
    ) -> None:
        """Initialize LLM caller.

        Args:
            model_name: Name of the current LLM model.
            web_search_classifier_fn: Optional web search classifier.
            memory_recall_classifier_fn: Optional memory recall classifier.
            broker: Tool broker for forced tool execution.
        """
        self.model_name = model_name
        self.web_search_classifier_fn = web_search_classifier_fn
        self.memory_recall_classifier_fn = memory_recall_classifier_fn
        self.broker = broker
        self.sse_queue: object | None = None

    async def call_once(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        call_usages: list[LLMCallUsage],
        tool_choice: object | None = None,
        stream_mode: LLMStreamMode = LLMStreamMode.ALL,
    ) -> object:
        """Call the LLM once with the currently prepared prompt messages."""
        llm_response = await self.invoke(
            llm_call=llm_call,
            messages=messages,
            openai_tools=openai_tools,
            tool_choice=tool_choice,
            stream_mode=stream_mode,
        )
        if llm_response.usage is not None:
            call_usages.append(llm_response.usage)
        return llm_response

    async def invoke(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        stream_mode: LLMStreamMode = LLMStreamMode.ALL,
        tool_choice: object | None = None,
    ) -> object:
        """Invoke the configured LLM with the supported optional arguments."""
        if stream_mode is not LLMStreamMode.DISABLED and llm_accepts_sse_queue(
            llm_call
        ):
            return await self.invoke_with_sse(
                llm_call=llm_call,
                messages=messages,
                openai_tools=openai_tools,
                tool_choice=tool_choice,
                stream_mode=stream_mode,
            )
        if llm_accepts_tools_override(llm_call):
            return await llm_call(
                messages,
                tools_override=openai_tools,
            )
        return await llm_call(messages)

    async def invoke_with_sse(
        self,
        *,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        tool_choice: object | None = None,
        stream_mode: LLMStreamMode = LLMStreamMode.ALL,
    ) -> object:
        """Invoke the configured LLM while forwarding tokens into the SSE queue."""
        stream_queue = self._stream_queue_for_mode(stream_mode)
        if llm_accepts_tools_override(llm_call):
            kwargs: dict[str, object] = {
                "sse_queue_override": stream_queue,
                "tools_override": openai_tools,
            }
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            return await llm_call(messages, **kwargs)
        return await llm_call(
            messages,
            stream_queue,
        )

    def _stream_queue_for_mode(self, stream_mode: LLMStreamMode) -> object | None:
        """Return the queue object that implements the requested stream policy."""
        if stream_mode is LLMStreamMode.ALL:
            return self.sse_queue
        if stream_mode is LLMStreamMode.THINKING_ONLY:
            return ThinkingOnlyStreamQueue(self.sse_queue)
        return None

    async def should_force_web_search(
        self,
        *,
        filtered_message: str,
        llm_call: object,
        call_usages: list[LLMCallUsage],
    ) -> bool:
        """Decide whether one turn must fetch web results before the first answer."""
        if message_requires_forced_web_search(filtered_message):
            return True
        if not message_may_need_forced_web_search_classifier(filtered_message):
            return False
        if self.web_search_classifier_fn is not None:
            return await self.web_search_classifier_fn(filtered_message)
        classifier_response = await self.invoke(
            llm_call=llm_call,
            messages=[
                {"role": "system", "content": FORCED_WEB_SEARCH_CLASSIFIER_PROMPT},
                {"role": "user", "content": filtered_message},
            ],
            openai_tools=None,
            stream_mode=LLMStreamMode.DISABLED,
        )
        if classifier_response.usage is not None:
            call_usages.append(classifier_response.usage)
        return llm_requires_forced_web_search(classifier_response.text)

    async def should_force_memory_recall(
        self,
        filtered_message: str,
    ) -> bool:
        """Decide whether one turn must recall memory before the first answer."""
        if self.memory_recall_classifier_fn is None:
            return False
        if not message_may_need_memory_recall(filtered_message):
            return False
        try:
            return await self.memory_recall_classifier_fn(filtered_message)
        except Exception:
            logger.warning(
                "Memory recall classification failed",
                exc_info=True,
            )
            return False

    async def append_forced_web_search_context(
        self,
        *,
        filtered_message: str,
        messages: list[dict[str, object]],
        llm_call: object,
        call_usages: list[LLMCallUsage],
    ) -> bool:
        """Run web_search early for explicit online-search requests."""
        try:
            should_force = await self.should_force_web_search(
                filtered_message=filtered_message,
                llm_call=llm_call,
                call_usages=call_usages,
            )
        except Exception:
            logger.warning(
                "Forced web_search classification failed",
                exc_info=True,
            )
            return False
        if not should_force:
            return False
        try:
            search_result = await self.broker.execute(
                "web_search",
                {"query": filtered_message},
            )
        except Exception:
            logger.warning(
                "Forced web_search preflight failed",
                exc_info=True,
            )
            return False
        if not search_result.success:
            logger.warning(
                "Forced web_search preflight returned an error: %s",
                search_result.result,
            )
            return False
        messages.append(
            {
                "role": "system",
                "content": (
                    "Web search results were fetched before the first answer "
                    "because the user explicitly asked to search online.\n"
                    f"{search_result.result}"
                ),
            }
        )
        return True

    async def append_required_tool_context(
        self,
        *,
        filtered_message: str,
        messages: list[dict[str, object]],
        requirements: ResponseRequirements,
    ) -> RequirementSatisfaction:
        """Execute deterministic mandatory tools before answer synthesis."""
        satisfaction = RequirementSatisfaction()
        if "web_search" in requirements.required_tools:
            evidence_plan = self._current_data_evidence_plan(
                filtered_message,
                requirements,
            )
            search_queries = list(evidence_plan.query_plan)
            search_results: list[tuple[str, object]] = []
            search_failures: dict[str, str] = {}
            for query in search_queries:
                try:
                    search_result = await self.broker.execute(
                        "web_search",
                        {"query": query},
                    )
                except Exception as exc:
                    logger.warning("Required web_search failed", exc_info=True)
                    search_failures[query] = str(exc)
                    continue
                if search_result.success:
                    search_results.append((query, search_result.result))
                else:
                    search_failures[query] = str(search_result.result)
            evidence_report = self._current_data_evidence_report(
                evidence_plan,
                search_results,
                search_failures,
            )
            if search_results:
                if len(search_results) == 1:
                    search_context = self._required_tool_context(search_results[0][1])
                else:
                    search_context = self._required_tool_context(
                        self._combined_required_search_result(search_results),
                    )
                if search_failures:
                    failures = "\n".join(
                        f"- {query}: {failure}"
                        for query, failure in search_failures.items()
                    )
                    search_context = f"{search_context}\n\nSearch failures:\n{failures}"
                search_context = (
                    f"{search_context}\n\n"
                    f"{self._current_data_evidence_context(evidence_report)}"
                )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Required web_search evidence was fetched before "
                            "answer synthesis because the request needs current "
                            "data. Use only this evidence for current values; "
                            "do not fill missing prices from memory.\n"
                            f"{search_context}"
                        ),
                    }
                )
                satisfaction = satisfaction.with_success(
                    "web_search"
                ).with_current_data_targets(
                    targets=evidence_report.target_labels,
                    missing_targets=evidence_report.missing_labels,
                )
            else:
                if len(search_failures) == 1:
                    failure_text = next(iter(search_failures.values()))
                else:
                    failure_text = (
                        "; ".join(
                            f"{query}: {failure}"
                            for query, failure in search_failures.items()
                        )
                        or "search returned no usable results"
                    )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Required web_search failed before answer synthesis. "
                            "If answering, clearly report that current data could "
                            f"not be fetched.\n{failure_text}"
                        ),
                    }
                )
                satisfaction = satisfaction.with_failure("web_search", failure_text)
        if "http_fetch" in requirements.required_tools:
            url = requirements.urls[0] if requirements.urls else ""
            if not url:
                satisfaction = satisfaction.with_failure(
                    "http_fetch",
                    "No URL found in request",
                )
            else:
                try:
                    fetch_result = await self.broker.execute(
                        "http_fetch",
                        {"url": url},
                    )
                except Exception as exc:
                    logger.warning("Required http_fetch failed", exc_info=True)
                    satisfaction = satisfaction.with_failure("http_fetch", str(exc))
                else:
                    if fetch_result.success:
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "Required http_fetch evidence was fetched "
                                    "before answer synthesis because the request "
                                    f"contains an explicit URL.\n{fetch_result.result}"
                                ),
                            }
                        )
                        satisfaction = satisfaction.with_success("http_fetch")
                    else:
                        failure_text = str(fetch_result.result)
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "Required http_fetch failed before answer "
                                    "synthesis. If answering, clearly report "
                                    "that the URL could not be fetched.\n"
                                    f"{failure_text}"
                                ),
                            }
                        )
                        satisfaction = satisfaction.with_failure(
                            "http_fetch",
                            failure_text,
                        )
        return satisfaction

    def _current_data_evidence_plan(
        self,
        message: str,
        requirements: ResponseRequirements,
    ) -> CurrentDataEvidencePlan:
        """Build deterministic search plan for mandatory current-data evidence."""
        normalized = message.casefold()
        targets = self._current_data_targets(message)
        if not requirements.needs_current_data:
            return CurrentDataEvidencePlan(message, (), (message,))
        if not any(marker in normalized for marker in PRICE_SEARCH_MARKERS):
            return CurrentDataEvidencePlan(message, targets, (message,))
        targeted_queries = [
            self._current_data_target_query(target) for target in targets
        ]
        queries: list[str] = []
        if len(targeted_queries) < 2:
            return CurrentDataEvidencePlan(message, targets, (message,))
        for query in targeted_queries:
            if query and query not in queries:
                queries.append(query)
        if message not in queries:
            queries.append(message)
        return CurrentDataEvidencePlan(
            original_query=message,
            targets=targets,
            query_plan=tuple(queries[:MAX_REQUIRED_WEB_SEARCH_QUERIES]),
        )

    def _required_web_search_queries(
        self,
        message: str,
        requirements: ResponseRequirements,
    ) -> list[str]:
        """Build deterministic search queries for mandatory current-data evidence."""
        return list(self._current_data_evidence_plan(message, requirements).query_plan)

    def _current_data_targets(self, message: str) -> tuple[CurrentDataTarget, ...]:
        """Extract requested current-data targets without user-facing modes."""
        normalized = message.casefold()
        targets: list[CurrentDataTarget] = []
        seen_labels: set[str] = set()
        seen_aliases: set[str] = set()
        for name, symbol, aliases in MARKET_TARGET_ALIAS_HINTS:
            if any(alias in normalized for alias in aliases):
                target = CurrentDataTarget(
                    label=name,
                    aliases=(symbol, *aliases),
                    kind="market",
                )
                targets.append(target)
                seen_labels.add(target.label.casefold())
                seen_aliases.update(alias.casefold() for alias in target.aliases)

        if targets and not TARGET_LIST_SEPARATOR_PATTERN.search(message):
            return tuple(targets)

        generic_text = CURRENT_DATA_FILLER_PATTERN.sub(" ", message)
        for part in TARGET_SPLIT_PATTERN.split(generic_text):
            label = self._normalize_current_data_target_label(part)
            normalized_label = label.casefold()
            if (
                not label
                or normalized_label in seen_labels
                or normalized_label in seen_aliases
            ):
                continue
            targets.append(CurrentDataTarget(label=label, aliases=(label,)))
            seen_labels.add(normalized_label)
        return tuple(targets[: MAX_REQUIRED_WEB_SEARCH_QUERIES - 1])

    def _normalize_current_data_target_label(self, value: str) -> str:
        """Normalize one candidate current-data target label."""
        label = " ".join(value.strip(" ?!:.()[]{}\"'`").split())
        if len(label) < MIN_GENERIC_TARGET_CHARS:
            return ""
        if not any(character.isalnum() for character in label):
            return ""
        return label

    def _current_data_target_query(self, target: CurrentDataTarget) -> str:
        """Build one bounded current-data search query for a target."""
        primary_alias = target.aliases[0] if target.aliases else ""
        label = target.label
        if primary_alias and primary_alias.casefold() != label.casefold():
            label = f"{label} {primary_alias}"
        return f"current {label} price USD today"

    def _current_data_evidence_report(
        self,
        plan: CurrentDataEvidencePlan,
        search_results: list[tuple[str, object]],
        search_failures: dict[str, str],
    ) -> CurrentDataEvidenceReport:
        """Build target-level evidence coverage from search results."""
        evidence: list[CurrentDataTargetEvidence] = []
        if not plan.targets:
            return CurrentDataEvidenceReport(plan=plan, target_evidence=())
        for target in plan.targets:
            query = self._current_data_target_query(target)
            result = next(
                (
                    result
                    for result_query, result in search_results
                    if result_query == query
                ),
                None,
            )
            if result is None and len(plan.targets) == 1:
                result = next(
                    (
                        result
                        for result_query, result in search_results
                        if result_query == plan.original_query
                    ),
                    None,
                )
            failure_reason = search_failures.get(query, "")
            found = self._search_result_has_evidence(result)
            sources, snippets = self._search_result_source_summary(result)
            evidence.append(
                CurrentDataTargetEvidence(
                    target=target,
                    found=found,
                    sources=sources,
                    snippets=snippets,
                    failure_reason=failure_reason,
                )
            )
        return CurrentDataEvidenceReport(
            plan=plan,
            target_evidence=tuple(evidence),
        )

    def _current_data_evidence_context(
        self,
        report: CurrentDataEvidenceReport,
    ) -> str:
        """Format target-level evidence coverage for answer synthesis."""
        if not report.target_evidence:
            return "Current-data evidence coverage: no explicit targets detected."
        lines = ["Current-data evidence coverage:"]
        for evidence in report.target_evidence:
            status = "found" if evidence.found else "missing"
            lines.append(f"- {evidence.target.label}: {status}")
            if evidence.sources:
                lines.append(f"  Sources: {', '.join(evidence.sources[:2])}")
            if evidence.failure_reason:
                lines.append(f"  Failure: {evidence.failure_reason}")
        missing_labels = report.missing_labels
        if missing_labels:
            lines.append(
                "Missing targets must be reported explicitly in the final answer: "
                f"{', '.join(missing_labels)}."
            )
        return "\n".join(lines)

    def _search_result_has_evidence(self, result: object | None) -> bool:
        """Return whether a tool result contains usable current-data evidence."""
        if result is None:
            return False
        parsed_result = result
        if isinstance(result, str):
            if not result.strip():
                return False
            try:
                parsed_result = literal_eval(result)
            except SyntaxError, ValueError:
                return bool(result.strip())
        if not isinstance(parsed_result, dict):
            return bool(str(parsed_result).strip())
        raw_results = parsed_result.get("results")
        if isinstance(raw_results, list):
            return any(isinstance(item, dict) and bool(item) for item in raw_results)
        return bool(str(parsed_result).strip())

    def _search_result_source_summary(
        self,
        result: object | None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Extract compact source names and snippets from one search result."""
        if result is None:
            return (), ()
        parsed_result = result
        if isinstance(result, str):
            try:
                parsed_result = literal_eval(result)
            except SyntaxError, ValueError:
                return (), (self._compact_search_field(result),)
        if not isinstance(parsed_result, dict):
            return (), (self._compact_search_field(parsed_result),)
        raw_results = parsed_result.get("results")
        if not isinstance(raw_results, list):
            return (), (self._compact_search_field(parsed_result),)
        sources: list[str] = []
        snippets: list[str] = []
        for item in raw_results[:MAX_REQUIRED_SEARCH_RESULTS_PER_QUERY]:
            if not isinstance(item, dict):
                continue
            title = self._compact_search_field(item.get("title", ""))
            url = self._compact_search_field(item.get("url", ""))
            snippet = self._compact_search_field(item.get("snippet", ""))
            if title or url:
                sources.append(title or url)
            if snippet:
                snippets.append(snippet)
        return tuple(sources), tuple(snippets)

    def _combined_required_search_result(
        self,
        search_results: list[tuple[str, object]],
    ) -> dict[str, object]:
        """Combine one or more web_search results into one prompt payload."""
        combined_results: list[dict[str, object]] = []
        text_blocks: list[str] = []
        for query, result in search_results:
            parsed_result = result
            if isinstance(result, str):
                try:
                    parsed_result = literal_eval(result)
                except SyntaxError, ValueError:
                    text_blocks.append(f"Query: {query}\n{result}")
                    continue
            if not isinstance(parsed_result, dict):
                text_blocks.append(f"Query: {query}\n{parsed_result}")
                continue
            raw_results = parsed_result.get("results")
            if not isinstance(raw_results, list):
                text_blocks.append(f"Query: {query}\n{parsed_result}")
                continue
            for item in raw_results[:MAX_REQUIRED_SEARCH_RESULTS_PER_QUERY]:
                if not isinstance(item, dict):
                    continue
                combined_results.append({"query": query, **item})
        return {
            "results": combined_results,
            "text_blocks": text_blocks,
        }

    def _required_tool_context(self, result: object) -> str:
        """Return compact prompt context for mandatory tool evidence."""
        parsed_result = result
        if isinstance(result, str):
            try:
                parsed_result = literal_eval(result)
            except SyntaxError, ValueError:
                return result[:MAX_REQUIRED_TOOL_CONTEXT_CHARS]
        if not isinstance(parsed_result, dict):
            return str(parsed_result)[:MAX_REQUIRED_TOOL_CONTEXT_CHARS]
        raw_text_blocks = parsed_result.get("text_blocks")
        raw_results = parsed_result.get("results")
        if not isinstance(raw_results, list):
            return str(parsed_result)[:MAX_REQUIRED_TOOL_CONTEXT_CHARS]
        lines = ["Search results:"]
        if isinstance(raw_text_blocks, list):
            for text_block in raw_text_blocks:
                block = self._compact_search_field(text_block)
                if block:
                    lines.append(f"Text: {block}")
        for index, item in enumerate(
            raw_results[:MAX_REQUIRED_SEARCH_RESULTS],
            start=1,
        ):
            if not isinstance(item, dict):
                continue
            title = self._compact_search_field(item.get("title", ""))
            url = self._compact_search_field(item.get("url", ""))
            snippet = self._compact_search_field(item.get("snippet", ""))
            query = self._compact_search_field(item.get("query", ""))
            lines.append(f"{index}. {title}")
            if query:
                lines.append(f"   Query: {query}")
            if url:
                lines.append(f"   URL: {url}")
            if snippet:
                lines.append(f"   Snippet: {snippet}")
        return "\n".join(lines)[:MAX_REQUIRED_TOOL_CONTEXT_CHARS]

    def _compact_search_field(self, value: object) -> str:
        """Normalize one search-result field for prompt context."""
        text = " ".join(str(value or "").split())
        if len(text) <= MAX_REQUIRED_SEARCH_FIELD_CHARS:
            return text
        return f"{text[:MAX_REQUIRED_SEARCH_FIELD_CHARS].rstrip()}..."

    async def append_forced_memory_recall_context(
        self,
        *,
        filtered_message: str,
        messages: list[dict[str, object]],
    ) -> int:
        """Run memory_recall early for questions about past conversations.

        Returns:
            Estimated token count of injected recall content (0 if nothing injected).
        """
        try:
            should_force = await self.should_force_memory_recall(
                filtered_message,
            )
        except Exception:
            logger.warning(
                "Forced memory_recall classification failed",
                exc_info=True,
            )
            return 0
        if not should_force:
            return 0
        try:
            recall_result = await self.broker.execute(
                "memory_recall",
                {"query": filtered_message},
            )
        except Exception:
            logger.warning(
                "Forced memory_recall preflight failed",
                exc_info=True,
            )
            return 0
        if not recall_result.success:
            return 0
        result_text = str(recall_result.result)
        if not result_text.strip():
            return 0
        recall_content = (
            f"Memory recall results from past conversations:\n{result_text}"
        )
        messages.append(
            {
                "role": "system",
                "content": recall_content,
            }
        )
        from backend.memory.context_builder.constants import estimate_tokens

        return estimate_tokens(recall_content)

    async def run_model_loop(
        self,
        *,
        loop: AgenticLoop,
        llm_call: object,
        messages: list[dict[str, object]],
        openai_tools: list[dict] | None,
        call_usages: list[LLMCallUsage],
        channel: str,
        tool_executor: ToolExecutor,
        user_intent_tools: set[str] | None = None,
        force_web_search: bool = False,
        requirements: ResponseRequirements | None = None,
        satisfaction: RequirementSatisfaction | None = None,
    ) -> ModelLoopResult:
        """Run the model/tool loop until a final answer is available."""
        raw_final_text = ""
        structured_content = None
        executed_immediate_tools = False
        requirements = requirements or ResponseRequirements()
        satisfaction = satisfaction or RequirementSatisfaction()
        required_tool_failure_answer = build_required_tool_failure_answer(
            requirements=requirements,
            satisfaction=satisfaction,
        )
        if required_tool_failure_answer:
            return ModelLoopResult(raw_final_text=required_tool_failure_answer)

        first_iteration = True
        while True:
            state = loop.check_limits()
            if state != LoopState.RUNNING:
                break

            forced_choice = None
            if first_iteration and force_web_search:
                forced_choice = {
                    "type": "function",
                    "function": {"name": "web_search"},
                }

            llm_response = await self.call_once(
                llm_call=llm_call,
                messages=messages,
                openai_tools=openai_tools,
                call_usages=call_usages,
                tool_choice=forced_choice,
                stream_mode=(
                    LLMStreamMode.THINKING_ONLY
                    if requirements.required_tools
                    else LLMStreamMode.ALL
                ),
            )
            first_iteration = False
            round_result = await tool_executor.run_one_tool_round(
                llm_response=llm_response,
                messages=messages,
                channel=channel,
                user_intent_tools=user_intent_tools,
            )
            if round_result.raw_final_text:
                raw_final_text = round_result.raw_final_text
                structured_content = round_result.structured_content
                break
            if round_result.executed_immediate_tools:
                executed_immediate_tools = True
            if not round_result.should_continue:
                break

            loop.advance()

        decision = validate_final_answer(
            raw_final_text=raw_final_text,
            structured_content=structured_content,
            requirements=requirements,
            satisfaction=satisfaction,
        )
        if decision.requires_recovery or (
            executed_immediate_tools and not raw_final_text.strip()
        ):
            recovered_response = await request_non_empty_final_answer(
                llm_call=llm_call,
                messages=messages,
                call_usages=call_usages,
            )
            if recovered_response is not None and recovered_response.answer.strip():
                raw_final_text = recovered_response.answer
                structured_content = recovered_response
                decision = validate_final_answer(
                    raw_final_text=raw_final_text,
                    structured_content=structured_content,
                    requirements=requirements,
                    satisfaction=satisfaction,
                )

        if raw_final_text.strip():
            if not decision.accepted:
                logger.warning(
                    "Final answer rejected after recovery: %s",
                    decision.reason,
                )
                return ModelLoopResult(
                    raw_final_text=(
                        "I could not produce a complete final answer for that "
                        "request. Please try again."
                    ),
                )
            if (
                structured_content is not None
                and len(structured_content.suggestions) < 3
            ):
                try:
                    suggestions = await request_missing_suggestions(
                        llm_call=llm_call,
                        messages=messages,
                        answer_text=structured_content.answer or raw_final_text,
                        call_usages=call_usages,
                    )
                except Exception:
                    logger.warning(
                        "Missing suggestions recovery failed",
                        exc_info=True,
                    )
                    suggestions = []
                if suggestions:
                    merged_suggestions = [
                        *structured_content.suggestions,
                        *[
                            suggestion
                            for suggestion in suggestions
                            if suggestion not in structured_content.suggestions
                        ],
                    ][:3]
                    structured_content = replace(
                        structured_content,
                        suggestions=merged_suggestions,
                    )
            return ModelLoopResult(
                raw_final_text=raw_final_text,
                structured_content=structured_content,
            )
        return ModelLoopResult(
            raw_final_text=(
                "I could not produce a final answer for that request. Please try again."
            ),
        )
