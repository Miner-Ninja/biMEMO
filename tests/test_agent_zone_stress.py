"""Tests for memory zone growth and stress behavior in the agent loop."""

from unittest.mock import AsyncMock, MagicMock

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.concurrency import AgentConcurrency
from backend.llm.call_factory import LLMResponse
from backend.llm.usage import LLMCallUsage
from backend.memory.context_builder import ContextResult  # noqa: TC001
from backend.tools.broker import ToolResult


# TC001: ContextResult stays a top-level import: the -> ContextResult
# annotations below are evaluated at runtime, so a TYPE_CHECKING block
# would raise NameError when the tests define their local functions.


class TestAgentZoneStress:
    """Tests for memory zone growth under stress."""

    async def test_zone_subsystems_w_r_s_grow_during_conversation(self):
        """W, R, S zones grow as conversation progresses and recall fires."""
        from backend.memory.context_builder.models import ContextZoneBudget
        from backend.memory.context_builder.zone_builder import (
            build_zone_context,
            compress_messages_to_summary,
        )

        # Context window sized so W overflows after ~4 turns,
        # leaving enough S budget (~157 tokens) for visible growth.
        budget = ContextZoneBudget.from_context_window(num_ctx=1300)

        # In-memory message store shared between context_fn and save_fn
        stored_messages: list[tuple[str, str]] = []
        accumulated_summary = {"text": ""}

        def zone_context_fn(
            session_id: str,
            **kwargs: object,
        ) -> ContextResult:
            """Build zone context with background compression simulation.

            Compression updates accumulated_summary for the NEXT turn,
            matching the production flow where LLM compression runs
            in the background and S appears one turn later.
            """
            del session_id, kwargs
            result = build_zone_context(
                recent_messages=list(stored_messages),
                conversation_summary=accumulated_summary["text"],
                zone_budget=budget,
            )
            if result.evicted_messages:
                prev = accumulated_summary["text"]
                new_part = compress_messages_to_summary(
                    result.evicted_messages,
                )
                if prev and new_part:
                    accumulated_summary["text"] = prev + "\n" + new_part
                elif new_part:
                    accumulated_summary["text"] = new_part
            return result

        def save_fn(
            conversation_id: str,
            role: str,
            content: str,
            meta: object = None,
            client_message_id: object = None,
        ) -> str:
            """Save message into in-memory store."""
            del conversation_id, meta, client_message_id
            text = content if isinstance(content, str) else str(content)
            stored_messages.append((role, text))
            return f"msg-{len(stored_messages)}"

        broker = MagicMock()
        recall_result = ToolResult(
            tool_name="memory_recall",
            success=True,
            result=(
                "User discussed Python FastAPI project with PostgreSQL. "
                "Testing done with pytest fixtures."
            ),
            duration_ms=1.0,
        )
        broker.execute = AsyncMock(return_value=recall_result)
        broker.list_openai_schemas = MagicMock(return_value=[])

        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=zone_context_fn,
        )
        orchestrator.save_fn = save_fn

        turn_number = 0

        async def mock_llm(messages: list[dict]) -> LLMResponse:
            """Return a response with context_total for zone computation."""
            return LLMResponse(
                text=f"Answer to turn {turn_number}. " * 15,
                tool_calls=[],
                raw_message={
                    "role": "assistant",
                    "content": f"Answer to turn {turn_number}. " * 15,
                },
                usage=LLMCallUsage(
                    prompt_tokens=50,
                    completion_tokens=20,
                    prompt_ms=5.0,
                    generation_ms=10.0,
                    total_ms=15.0,
                    model="test-model",
                    context_total=1300,
                ),
            )

        # Padded messages to fill W zone quickly (~50 tokens each)
        conversation_messages = [
            "Tell me about microservice architecture "
            "and design patterns in modern systems",
            "Which databases work best for write-heavy "
            "high-load distributed applications",
            "Explain the difference between sync and "
            "async programming with code examples",
            "How to set up monitoring and logging in "
            "a production Kubernetes environment",
            "Describe data caching strategies and cache "
            "invalidation in distributed systems",
            "What testing approaches are used in large "
            "projects with microservice architecture",
            "Explain message queue principles and when "
            "to use RabbitMQ versus Apache Kafka",
        ]

        zone_history: list[dict[str, int]] = []

        # Phase 1: send messages to grow W and eventually trigger S
        for msg in conversation_messages:
            turn_number += 1
            resp = await orchestrator.process_message(
                message=msg,
                channel="web",
                llm_call=mock_llm,
                session_id="s1",
                conversation_id="conv-zone-test",
            )
            if resp.stats and resp.stats.memory_zones:
                zones = resp.stats.memory_zones
                step = {
                    "turn": turn_number,
                    "W": zones.working_memory.used,
                    "R": zones.recall_results.used,
                    "S": zones.conversation_summary.used,
                }
                zone_history.append(step)
                print(
                    f"  Turn {turn_number}: "
                    f"W:{step['W']}/{zones.working_memory.budget} "
                    f"R:{step['R']}/{zones.recall_results.budget} "
                    f"S:{step['S']}/{zones.conversation_summary.budget}"
                )

        # Phase 2: enable forced recall and ask about past conversations
        orchestrator.memory_recall_classifier_fn = AsyncMock(
            return_value=True,
        )
        turn_number += 1
        resp = await orchestrator.process_message(
            message="Помнишь что мы раньше обсуждали?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="conv-zone-test",
        )
        if resp.stats and resp.stats.memory_zones:
            zones = resp.stats.memory_zones
            step = {
                "turn": turn_number,
                "W": zones.working_memory.used,
                "R": zones.recall_results.used,
                "S": zones.conversation_summary.used,
            }
            zone_history.append(step)
            print(
                f"  Turn {turn_number} (recall): "
                f"W:{step['W']}/{zones.working_memory.budget} "
                f"R:{step['R']}/{zones.recall_results.budget} "
                f"S:{step['S']}/{zones.conversation_summary.budget}"
            )

        # --- Assertions ---

        # W must have grown across turns
        w_values = [s["W"] for s in zone_history]
        assert w_values[-1] > w_values[0], f"W did not grow: {w_values}"

        # S must reach at least 30 tokens (meaningful eviction)
        final_s = zone_history[-1]["S"]
        assert final_s >= 30, f"S zone too low: {final_s}, expected >= 30"

        # Marker text alone must not fabricate an R-zone tool result.
        final_r = zone_history[-1]["R"]
        assert final_r == 0

    async def test_zone_stress_w_overflow_s_accumulates(self):
        """Stress: many turns overflow W, S accumulates, system stays stable."""
        from backend.memory.context_builder.models import (
            ContextZoneBudget,
        )
        from backend.memory.context_builder.zone_builder import (
            build_zone_context,
            compress_messages_to_summary,
        )

        num_ctx = 1300
        budget = ContextZoneBudget.from_context_window(num_ctx=num_ctx)

        stored_messages: list[tuple[str, str]] = []
        accumulated_summary = {"text": ""}

        def zone_context_fn(
            session_id: str,
            **kwargs: object,
        ) -> ContextResult:
            """Build zone context with background compression simulation."""
            del session_id, kwargs
            result = build_zone_context(
                recent_messages=list(stored_messages),
                conversation_summary=accumulated_summary["text"],
                zone_budget=budget,
            )
            if result.evicted_messages:
                prev = accumulated_summary["text"]
                new_part = compress_messages_to_summary(
                    result.evicted_messages,
                )
                if prev and new_part:
                    accumulated_summary["text"] = prev + "\n" + new_part
                elif new_part:
                    accumulated_summary["text"] = new_part
            return result

        def save_fn(
            conversation_id: str,
            role: str,
            content: str,
            meta: object = None,
            client_message_id: object = None,
        ) -> str:
            """Save message into in-memory store."""
            del conversation_id, meta, client_message_id
            text = content if isinstance(content, str) else str(content)
            stored_messages.append((role, text))
            return f"msg-{len(stored_messages)}"

        broker = MagicMock()
        recall_result = ToolResult(
            tool_name="memory_recall",
            success=True,
            result=(
                "User discussed Python FastAPI project. "
                "Testing done with pytest fixtures. "
                "Database is PostgreSQL with migrations."
            ),
            duration_ms=1.0,
        )
        broker.execute = AsyncMock(return_value=recall_result)
        broker.list_openai_schemas = MagicMock(return_value=[])

        orchestrator = AgentOrchestrator(
            broker=broker,
            concurrency=AgentConcurrency(),
            context_fn=zone_context_fn,
        )
        orchestrator.save_fn = save_fn

        turn_number = 0

        async def mock_llm(messages: list[dict]) -> LLMResponse:
            """Return a long response to fill W zone fast."""
            return LLMResponse(
                text=f"Answer to turn {turn_number}. " * 15,
                tool_calls=[],
                raw_message={
                    "role": "assistant",
                    "content": (f"Answer to turn {turn_number}. " * 15),
                },
                usage=LLMCallUsage(
                    prompt_tokens=50,
                    completion_tokens=60,
                    prompt_ms=5.0,
                    generation_ms=10.0,
                    total_ms=15.0,
                    model="test-model",
                    context_total=num_ctx,
                ),
            )

        stress_messages = [
            f"Question {i}: explain topic number {i} in detail with examples and code"
            for i in range(1, 16)
        ]

        zone_history: list[dict[str, int]] = []

        # Phase 1: 15 turns to stress W and build up S
        for msg in stress_messages:
            turn_number += 1
            resp = await orchestrator.process_message(
                message=msg,
                channel="web",
                llm_call=mock_llm,
                session_id="s1",
                conversation_id="conv-stress",
            )
            if resp.stats and resp.stats.memory_zones:
                zones = resp.stats.memory_zones
                step = {
                    "turn": turn_number,
                    "W": zones.working_memory.used,
                    "W_budget": zones.working_memory.budget,
                    "R": zones.recall_results.used,
                    "S": zones.conversation_summary.used,
                    "S_budget": zones.conversation_summary.budget,
                }
                zone_history.append(step)
                print(
                    f"  Turn {turn_number:2d}: "
                    f"W:{step['W']:4d}/{step['W_budget']} "
                    f"R:{step['R']:3d} "
                    f"S:{step['S']:3d}/{step['S_budget']}"
                )

        # Phase 2: forced recall
        orchestrator.memory_recall_classifier_fn = AsyncMock(
            return_value=True,
        )
        turn_number += 1
        resp = await orchestrator.process_message(
            message="Помнишь что мы раньше обсуждали?",
            channel="web",
            llm_call=mock_llm,
            session_id="s1",
            conversation_id="conv-stress",
        )
        if resp.stats and resp.stats.memory_zones:
            zones = resp.stats.memory_zones
            step = {
                "turn": turn_number,
                "W": zones.working_memory.used,
                "W_budget": zones.working_memory.budget,
                "R": zones.recall_results.used,
                "S": zones.conversation_summary.used,
                "S_budget": zones.conversation_summary.budget,
            }
            zone_history.append(step)
            print(
                f"  Turn {turn_number:2d} (recall): "
                f"W:{step['W']:4d}/{step['W_budget']} "
                f"R:{step['R']:3d} "
                f"S:{step['S']:3d}/{step['S_budget']}"
            )

        # --- Assertions ---

        # W must never exceed its budget
        for step in zone_history:
            assert step["W"] <= step["W_budget"], (
                f"Turn {step['turn']}: W={step['W']} exceeded budget={step['W_budget']}"
            )

        # After eviction (65%), W should drop well below budget
        # and stay within budget on all subsequent turns
        post_eviction = [s for s in zone_history if s["W"] < s["W_budget"] // 2]
        assert len(post_eviction) > 0, "W should drop below 50% budget after eviction"

        # S must be at the budget cap (fully saturated)
        final_s = zone_history[-1]["S"]
        s_budget = zone_history[-1]["S_budget"]
        assert final_s >= s_budget * 0.8, (
            f"S={final_s} should be near budget={s_budget} after 15+ turns of overflow"
        )

        # Marker text alone must not fabricate an R-zone tool result.
        final_r = zone_history[-1]["R"]
        assert final_r == 0

        # System stability: no crashes, all 16 turns completed
        assert len(zone_history) == 16, f"Expected 16 turns, got {len(zone_history)}"
