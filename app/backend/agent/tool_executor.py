"""Tool execution delegate for the gateway orchestrator.

Handles building tool calls from LLM responses, executing them
through registry authorization, emitting progress notifications,
and queuing required confirmations. Manages the pending-call state for the
orchestrator's public API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    build_tool_result_messages,
)
from backend.agent.agent_loop_models import (
    ToolCall,
    ToolRoundResult,
)
from backend.agent.stage_timings import StageTimings, humanize_stage_label
from backend.permissions import (
    ActionContext,
    ActionDecision,
    ActionDecisionService,
    DecisionKind,
    PermissionMode,
)
from backend.security.provenance import serialize_tool_result
from backend.tools.broker import (
    AuthorizationContext,
    PreparedInvocation,
    ToolBroker,
    ToolResult,
)


if TYPE_CHECKING:
    from backend.trust.confirmation import ConfirmationManager, PendingAction

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Execute tool calls with trust zone enforcement.

    Attributes:
        broker: Tool broker for execution.
        confirmation_mgr: Manager for red-zone confirmations.
    """

    def __init__(
        self,
        broker: ToolBroker,
        confirmation_mgr: ConfirmationManager,
        action_decisions: ActionDecisionService | None = None,
        turn_store: object | None = None,
    ) -> None:
        """Initialize tool executor.

        Args:
            broker: Tool broker for execution.
            confirmation_mgr: Manager for red-zone confirmations.
            action_decisions: Optional action decision service.
            turn_store: Optional core-authority turn persistence client.
        """
        self.broker = broker
        self.confirmation_mgr = confirmation_mgr
        if action_decisions is not None:
            self.action_decisions = action_decisions
        else:
            self.action_decisions = ActionDecisionService(
                registry=broker.registry,
                workspace_roots=broker.workspace_roots,
            )
        self.turn_store = turn_store
        self.sse_queue: object | None = None
        self._pending_red: list[PendingAction] = []
        self.tool_call_count: int = 0
        self.tool_names_used: list[str] = []
        self.executed_tools: list[dict] = []
        self.allowed_tool_names: set[str] | None = None
        self.allowed_tool_revisions: dict[str, str] = {}
        # Context propagated to tool handlers via the broker
        # (e.g. {"conversation_id": ..., "session_id": ...}).
        # Set by the orchestrator before processing a turn.
        self.current_context: dict[str, str] = {}
        # Per-turn processing-stage accumulator, assigned by the orchestrator
        # before each turn so tool durations join recall/context timings.
        self.stage_timings: StageTimings | None = None
        # Invocation identity for idempotent red-zone queueing.
        self._last_invocation_id: tuple[str, ...] | None = None
        self._pending_continuation: dict[str, object] | None = None
        self.current_obligations: tuple[object, ...] = ()
        self._authorized_grants: dict[str, dict[str, object]] = {}
        self._prepared_calls: dict[str, object] = {}
        self._policy_decisions: dict[str, ActionDecision] = {}

    @property
    def _core_turn_id(self) -> str:
        """Return the active core-authority turn id or an empty string."""
        return self.current_context.get("agent_run_id", "")

    @property
    def _uses_registry_policy(self) -> bool:
        """Require the sole supported permission-policy version."""
        return self.current_context.get("tool_policy_version") == "registry-v1"

    def _invocation_id(self, tool_call_id: str) -> str:
        """Derive one stable invocation identity within the active turn."""
        turn_id = self._core_turn_id
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{turn_id}:call:{tool_call_id}"))

    def build_tool_calls(self, llm_response: object) -> list[ToolCall]:
        """Convert calls and collapse identical tool/argument requests."""
        self._prepared_calls = {}
        self._policy_decisions = {}
        calls: list[ToolCall] = []
        seen: set[str] = set()
        for tool_call in llm_response.tool_calls:
            call = ToolCall(
                id=tool_call.id,
                name=tool_call.name,
                parameters=tool_call.arguments,
            )
            signature = json.dumps(
                [call.name, call.parameters],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            calls.append(call)
        return calls

    @staticmethod
    def _assistant_message_for_calls(
        llm_response: object,
        calls: list[ToolCall],
    ) -> dict[str, object]:
        """Keep the assistant transcript consistent with deduplicated calls."""
        assistant_message = dict(llm_response.raw_message)
        raw_calls = assistant_message.get("tool_calls")
        if isinstance(raw_calls, list):
            allowed_ids = {call.id for call in calls}
            assistant_message["tool_calls"] = [
                item
                for item in raw_calls
                if isinstance(item, dict) and str(item.get("id", "")) in allowed_ids
            ]
        assistant_message["content"] = ""
        return assistant_message

    async def _execute_with_status(self, tool_call: ToolCall) -> ToolResult:
        """Execute one tool call and emit SSE status events around it."""
        self.tool_call_count += 1
        self.tool_names_used.append(tool_call.name)
        # Record the requested tool.
        self.executed_tools.append(
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "parameters": dict(tool_call.parameters),
                "permission_mode": "unknown",
                "status": "requested",
                "success": False,
            }
        )
        # Allowlist enforcement (used by scheduler for cron tasks)
        if (
            self.allowed_tool_names is not None
            and tool_call.name not in self.allowed_tool_names
        ):
            msg = (
                f"Tool '{tool_call.name}' is not in the allowlist "
                "for this scheduled task"
            )
            self.executed_tools[-1]["status"] = "failed"
            self.executed_tools[-1]["error"] = msg
            raise PermissionError(msg)
        decision = self._decide_tool_action(tool_call)
        if decision.kind == DecisionKind.BLOCK:
            msg = f"Tool action blocked: {decision.reason}"
            self.executed_tools[-1]["permission_mode"] = str(
                decision.details.get("permission_mode", "blocked")
            )
            self.executed_tools[-1]["status"] = "blocked"
            self.executed_tools[-1]["error"] = decision.reason
            if self.sse_queue is not None:
                await self.sse_queue.put(
                    {"type": "tool_status", "name": tool_call.name, "status": "error"}
                )
            prepared = self._prepared_calls.get(tool_call.id)
            if isinstance(self.broker, ToolBroker) and isinstance(
                prepared, PreparedInvocation
            ):
                self.broker.record_authorization_outcome(
                    prepared=prepared,
                    authorization=AuthorizationContext(
                        permission_mode=str(
                            decision.details.get("permission_mode", "blocked")
                        ),
                        policy_revision=str(
                            decision.details.get("policy_revision", "registry-v1")
                        ),
                        source=str(decision.details.get("source", "registry_policy")),
                    ),
                    outcome="tool_blocked",
                    reason=decision.reason,
                    correlation={
                        **self.current_context,
                        "invocation_id": (
                            self._invocation_id(tool_call.id)
                            if self._core_turn_id
                            else str(tool_call.id)
                        ),
                    },
                )
            raise PermissionError(msg)
        if self.sse_queue is not None:
            await self.sse_queue.put(
                {"type": "tool_status", "name": tool_call.name, "status": "started"}
            )
        # Push the current execution context into the broker so handlers
        # that need it (e.g. schedule_task) can read conversation_id from
        # broker.current_context. Cleared in finally to keep state clean.
        prepared = self._prepared_calls.get(tool_call.id)
        if isinstance(self.broker, ToolBroker) and not isinstance(
            prepared, PreparedInvocation
        ):
            raise RuntimeError("Prepared invocation is required for execution")
        claim_generation: int | None = None
        invocation_id = self._invocation_id(tool_call.id) if self._core_turn_id else ""
        if invocation_id and self.turn_store is not None:
            grant = self._authorized_grants.get(tool_call.id)
            if grant is not None:
                if not isinstance(prepared, PreparedInvocation):
                    raise RuntimeError("Prepared invocation is required for grant use")
                claim = await self.turn_store.authorize_agent_invocation(
                    turn_id=self._core_turn_id,
                    invocation_id=invocation_id,
                    idempotency_key=f"{self._core_turn_id}:{invocation_id}",
                    capability=tool_call.name,
                    channel=self.current_context.get("channel", ""),
                    scope_type=str(grant["scope_type"]),
                    scope_value=str(grant["scope_value"]),
                    scope_json=dict(grant["scope"]),
                    scope_hash=str(grant["scope_hash"]),
                    policy_revision=str(grant["policy_revision"]),
                    canonical_args_hash=prepared.canonical_hash,
                )
            else:
                claim = await self.turn_store.claim_agent_invocation(
                    turn_id=self._core_turn_id,
                    invocation_id=invocation_id,
                    idempotency_key=f"{self._core_turn_id}:{invocation_id}",
                )
            claim_generation = int(claim["claim_generation"])
        execution_context = {
            **self.current_context,
            "invocation_id": invocation_id or str(tool_call.id),
        }
        self.broker.set_context(execution_context)
        grant = self._authorized_grants.get(tool_call.id)
        preauthorized = self._scheduled_task_preauthorized(tool_call)
        source = (
            "saved_grant"
            if grant is not None
            else "scheduled_task"
            if preauthorized
            else str(decision.details.get("source", "registry_policy"))
        )
        authorization = AuthorizationContext(
            permission_mode=str(decision.details.get("permission_mode", "implicit")),
            policy_revision=str(decision.details.get("policy_revision", "registry-v1")),
            source=source,
            authorization_id=(
                str(grant.get("authorization_id", ""))
                if grant is not None
                else str(self.current_context.get("scheduled_task_id", ""))
                if preauthorized
                else ""
            ),
        )
        try:
            result = await self.broker.execute_prepared(
                prepared,
                authorization,
                correlation=execution_context,
            )
        except Exception as exc:
            if self.sse_queue is not None:
                await self.sse_queue.put(
                    {"type": "tool_status", "name": tool_call.name, "status": "error"}
                )
            self.executed_tools[-1]["status"] = "failed"
            self.executed_tools[-1]["error"] = str(exc)
            if claim_generation is not None and self.turn_store is not None:
                await self.turn_store.complete_agent_invocation(
                    turn_id=self._core_turn_id,
                    invocation_id=invocation_id,
                    claim_generation=claim_generation,
                    status="failed",
                    error=str(exc),
                )
            raise
        finally:
            self.broker.clear_context()
        if self.stage_timings is not None:
            self.stage_timings.record(
                humanize_stage_label(tool_call.name), result.duration_ms
            )
        envelope_invocation_id = (
            invocation_id
            or (tool_call.id if isinstance(tool_call.id, str) else "")
            or str(uuid.uuid4())
        )
        result_envelope = json.loads(
            serialize_tool_result(
                result.result,
                tool_name=tool_call.name,
                invocation_id=envelope_invocation_id,
            )
        )
        mandatory_result_truncated = bool(
            result.success
            and result_envelope["truncated"]
            and any(
                getattr(item, "required_tool", None) == tool_call.name
                for item in self.current_obligations
            )
        )
        effective_success = bool(result.success and not mandatory_result_truncated)
        self.executed_tools[-1]["permission_mode"] = (
            result.authorization.permission_mode
        )
        self.executed_tools[-1]["authorization_source"] = result.authorization.source
        self.executed_tools[-1]["success"] = effective_success
        self.executed_tools[-1]["status"] = (
            "succeeded" if effective_success else "failed"
        )
        if not effective_success:
            self.executed_tools[-1]["error"] = str(result.result)
        if claim_generation is not None and self.turn_store is not None:
            await self.turn_store.complete_agent_invocation(
                turn_id=self._core_turn_id,
                invocation_id=invocation_id,
                claim_generation=claim_generation,
                status="succeeded" if effective_success else "failed",
                result={
                    "data": result.result,
                    "duration_ms": result.duration_ms,
                    "permission_mode": result.authorization.permission_mode,
                    "authorization_source": result.authorization.source,
                }
                if effective_success
                else None,
                error=(
                    None
                    if effective_success
                    else "mandatory tool result exceeded the evidence byte limit"
                    if mandatory_result_truncated
                    else str(result.result)
                ),
            )
        if self.sse_queue is not None:
            await self.sse_queue.put(
                {"type": "tool_status", "name": tool_call.name, "status": "completed"}
            )
        return result

    async def _persist_core_batch(
        self,
        *,
        calls: list[ToolCall],
        red_actions: dict[str, PendingAction],
        messages: list[dict[str, object]],
        assistant_message: dict[str, object],
    ) -> None:
        """Persist a complete sibling batch before any external dispatch."""
        if not self._core_turn_id or self.turn_store is None:
            return
        call_ids = ":".join(call.id for call in calls)
        batch_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{self._core_turn_id}:batch:{call_ids}")
        )
        obligations = list(self.current_obligations)
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.confirmation_mgr.timeout)
        ).isoformat()
        invocations: list[dict[str, object]] = []
        for call in calls:
            action = red_actions.get(call.id)
            prepared = self._prepared_calls.get(call.id)
            if not isinstance(prepared, PreparedInvocation):
                raise RuntimeError("Prepared invocation is required before persistence")
            candidates = [
                str(item.obligation_id)
                for item in obligations
                if getattr(item, "required_tool", None) == call.name
            ]
            invocations.append(
                {
                    "invocation_id": self._invocation_id(call.id),
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "arguments": dict(prepared.canonical_parameters),
                    "canonical_args_hash": prepared.canonical_hash,
                    "policy_revision": prepared.definition.permission.revision,
                    "source": "model",
                    "candidate_obligation_ids": candidates,
                    "confirmation_id": action.action_id if action is not None else None,
                    "confirmation_title": call.name if action is not None else "",
                    "confirmation_description": (
                        f"Allow {call.name} with the proposed parameters"
                        if action is not None
                        else ""
                    ),
                    "confirmation_expires_at": (
                        expires_at if action is not None else None
                    ),
                }
            )
        await self.turn_store.upsert_agent_tool_batch(
            turn_id=self._core_turn_id,
            batch_id=batch_id,
            assistant_message=assistant_message,
            transcript_snapshot=messages,
            invocations=invocations,
        )

    def _action_context(self, tool_call: ToolCall) -> ActionContext:
        """Build the action policy context for one tool call."""
        prepared = self._prepared_calls.get(tool_call.id)
        if prepared is None and isinstance(self.broker, ToolBroker):
            prepared = self.broker.prepare(tool_call.name, tool_call.parameters)
            self._prepared_calls[tool_call.id] = prepared
        return ActionContext(
            conversation_id=self.current_context.get("conversation_id", ""),
            channel=self.current_context.get("channel", ""),
            session_id=self.current_context.get("session_id", ""),
            tool_name=tool_call.name,
            parameters=tool_call.parameters,
            tool_policy_version=self.current_context.get("tool_policy_version", ""),
            prepared=prepared if isinstance(prepared, PreparedInvocation) else None,
        )

    def _decide_tool_action(self, tool_call: ToolCall) -> ActionDecision:
        """Evaluate one tool call against the action policy."""
        decision = self._policy_decisions.get(tool_call.id)
        if decision is None:
            decision = self.action_decisions.decide(self._action_context(tool_call))
            self._policy_decisions[tool_call.id] = decision
        return decision

    def _scheduled_task_preauthorized(self, call: ToolCall) -> bool:
        """Whether an autonomous scheduled run pre-authorises this RED tool.

        A scheduled task's own allowlist is the user's explicit authorization, so
        a confirmation-required tool in it runs without an interactive
        confirmation that can never happen in an autonomous run. Bounded to the
        scheduler channel and the task allowlist only.
        """
        decision = self._decide_tool_action(call)
        if not bool(decision.details.get("scheduler_preauthorizable", False)):
            return False
        return (
            self.current_context.get("channel") == "scheduler"
            and self.allowed_tool_names is not None
            and call.name in self.allowed_tool_names
            and self.allowed_tool_revisions.get(call.name)
            == str(decision.details.get("policy_revision", ""))
        )

    def _grant_coordinates(
        self,
        decision: ActionDecision,
    ) -> dict[str, object]:
        """Return stable storage coordinates for one reusable permission."""
        details = decision.details
        mode = str(details.get("permission_mode", ""))
        conversation_id = self.current_context.get("conversation_id", "")
        scope_hash = str(details.get("scope_hash", ""))
        scope_value = (
            conversation_id
            if mode == "conversation_capability"
            else f"{conversation_id}:{scope_hash}"
        )
        return {
            "scope_type": mode,
            "scope_value": scope_value,
            "scope": dict(details.get("scope", {})),
            "scope_hash": scope_hash,
            "policy_revision": str(details.get("policy_revision", "")),
        }

    async def _partition_structured_calls(
        self,
        calls: list[ToolCall],
    ) -> tuple[list[ToolCall], list[ToolCall]]:
        """Apply registry policy and authoritative grants to a native batch."""
        immediate: list[ToolCall] = []
        confirmation: list[ToolCall] = []
        self._authorized_grants = {}
        for call in calls:
            decision = self._decide_tool_action(call)
            if decision.kind == DecisionKind.BLOCK:
                prepared = self._prepared_calls.get(call.id)
                if isinstance(prepared, PreparedInvocation):
                    self.broker.record_authorization_outcome(
                        prepared=prepared,
                        authorization=AuthorizationContext(
                            permission_mode=str(
                                decision.details.get("permission_mode", "blocked")
                            ),
                            policy_revision=str(
                                decision.details.get("policy_revision", "registry-v1")
                            ),
                            source=str(
                                decision.details.get("source", "registry_policy")
                            ),
                        ),
                        outcome="tool_blocked",
                        reason=decision.reason,
                        correlation={
                            **self.current_context,
                            "invocation_id": self._invocation_id(call.id),
                        },
                    )
                raise PermissionError(f"Tool action blocked: {decision.reason}")
            preauthorized = self._scheduled_task_preauthorized(call)
            if decision.kind == DecisionKind.ALLOW or preauthorized:
                immediate.append(call)
                continue
            mode = str(decision.details.get("permission_mode", ""))
            if mode == "per_invocation":
                confirmation.append(call)
                continue
            coordinates = self._grant_coordinates(decision)
            grant = None
            if self.turn_store is not None:
                grant = await self.turn_store.lookup_capability_grant(
                    scope_type=str(coordinates["scope_type"]),
                    scope_value=str(coordinates["scope_value"]),
                    capability=call.name,
                    channel=self.current_context.get("channel", ""),
                    scope_hash=str(coordinates["scope_hash"]),
                    policy_revision=str(coordinates["policy_revision"]),
                )
            if grant is None or grant.get("decision") != "allow":
                confirmation.append(call)
                continue
            self._authorized_grants[call.id] = coordinates
            self._authorized_grants[call.id]["authorization_id"] = str(
                grant.get("id", "")
            )
            immediate.append(call)
        return immediate, confirmation

    def _promote_saved_grants(
        self,
        red_calls: list[ToolCall],
    ) -> tuple[list[ToolCall], list[ToolCall]]:
        """Move already granted RED calls into the immediate bucket."""
        immediate: list[ToolCall] = []
        still_red: list[ToolCall] = []
        for call in red_calls:
            decision = self._decide_tool_action(call)
            if decision.kind == DecisionKind.ALLOW:
                immediate.append(call)
            elif decision.kind == DecisionKind.BLOCK:
                raise PermissionError(f"Tool action blocked: {decision.reason}")
            elif self._scheduled_task_preauthorized(call):
                immediate.append(call)
            else:
                still_red.append(call)
        return immediate, still_red

    async def execute_immediate_tool_calls(
        self,
        immediate_calls: list[ToolCall],
    ) -> list[ToolResult]:
        """Run all non-confirmation tool calls for one model round."""
        return list(
            await asyncio.gather(
                *[
                    self._execute_containing_failure(tool_call)
                    for tool_call in immediate_calls
                ]
            )
        )

    async def _execute_containing_failure(self, tool_call: ToolCall) -> ToolResult:
        """Run one tool call, converting a failure into a failed result.

        One failing tool must not abort its siblings mid-flight nor tear
        down a round whose red confirmations are already persisted — the
        model sees the error in the tool result and reacts to it.
        """
        try:
            return await self._execute_with_status(tool_call)
        except Exception as exc:
            logger.exception("Tool %s failed during batch round", tool_call.name)
            return ToolResult(
                tool_name=tool_call.name,
                success=False,
                result=f"Tool execution failed: {exc}",
                duration_ms=0.0,
                authorization=AuthorizationContext(
                    permission_mode="unspecified",
                    policy_revision="registry-v1",
                    source="execution_error",
                ),
            )

    async def _activate_core_red_confirmations(
        self,
        *,
        red_calls: list[ToolCall],
        actions: dict[str, PendingAction],
        messages: list[dict[str, object]],
        assistant_message: dict[str, object],
        required_tools: tuple[str, ...],
    ) -> None:
        """Expose confirmations already committed with the complete core batch."""
        if not red_calls:
            return
        invocation_key = tuple(call.id for call in red_calls)
        self._last_invocation_id = invocation_key
        self._pending_continuation = {
            "tool_call_ids": invocation_key,
            "messages": messages,
            "assistant_message": assistant_message,
            "required_tools": list(required_tools),
        }
        pending_actions = [actions[call.id] for call in red_calls]
        for call, action in zip(red_calls, pending_actions, strict=True):
            await self._emit_waiting_confirmation(call, action.action_id)
            self.executed_tools.append(
                {
                    "id": call.id,
                    "name": call.name,
                    "parameters": dict(call.parameters),
                    "permission_mode": PermissionMode.PER_INVOCATION.value,
                    "status": "pending",
                    "success": False,
                    "invocation_id": self._invocation_id(call.id),
                    "confirmation_id": action.action_id,
                }
            )
        self._pending_red = pending_actions

    async def _emit_waiting_confirmation(
        self,
        call: ToolCall,
        action_id: str,
    ) -> None:
        """Emit a stream event for one action awaiting confirmation."""
        if self.sse_queue is None:
            return
        await self.sse_queue.put(
            {
                "type": "confirmation",
                "status": "waiting_confirmation",
                "action_id": action_id,
                "tool_name": call.name,
                "parameters": call.parameters,
            }
        )

    def append_tool_results_to_messages(
        self,
        *,
        messages: list[dict[str, object]],
        llm_response: object,
        immediate_calls: list[ToolCall],
        results: list[ToolResult],
    ) -> None:
        """Append the raw assistant tool-call turn and tool results."""
        if not results:
            return
        assistant_message = self._assistant_message_for_calls(
            llm_response,
            immediate_calls,
        )
        messages.append(assistant_message)
        invocation_ids = (
            {call.id: self._invocation_id(call.id) for call in immediate_calls}
            if self._core_turn_id
            else None
        )
        messages.extend(
            build_tool_result_messages(
                immediate_calls,
                results,
                invocation_ids=invocation_ids,
            )
        )

    async def run_one_tool_round(
        self,
        *,
        llm_response: object,
        messages: list[dict[str, object]],
        channel: str,
        required_tools: tuple[str, ...] = (),
    ) -> ToolRoundResult:
        """Execute one batch of tool calls and update the prompt state."""
        if not llm_response.tool_calls:
            return ToolRoundResult(
                raw_final_text=llm_response.text,
                structured_content=llm_response.structured_content,
                executed_immediate_tools=False,
                should_continue=False,
            )
        if not self._core_turn_id:
            raise RuntimeError("Tool execution requires a core-authority turn")

        tool_calls = self.build_tool_calls(llm_response)
        assistant_message = self._assistant_message_for_calls(
            llm_response,
            tool_calls,
        )
        if not self._uses_registry_policy:
            raise PermissionError("Unsupported tool policy version")
        immediate, red = await self._partition_structured_calls(tool_calls)
        core_actions: dict[str, PendingAction] = {}
        if self._core_turn_id:
            core_actions = {
                call.id: self.confirmation_mgr.request_confirmation(
                    call.name,
                    call.parameters,
                    channel,
                )
                for call in red
            }
            await self._persist_core_batch(
                calls=tool_calls,
                red_actions=core_actions,
                messages=messages,
                assistant_message=assistant_message,
            )
        results = await self.execute_immediate_tool_calls(immediate)
        await self._activate_core_red_confirmations(
            red_calls=red,
            actions=core_actions,
            messages=messages,
            assistant_message=assistant_message,
            required_tools=required_tools,
        )
        self.append_tool_results_to_messages(
            messages=messages,
            llm_response=llm_response,
            immediate_calls=immediate,
            results=results,
        )

        if red:
            return ToolRoundResult(
                raw_final_text=llm_response.text,
                structured_content=llm_response.structured_content,
                executed_immediate_tools=bool(results),
                should_continue=False,
            )

        return ToolRoundResult(
            raw_final_text="",
            structured_content=None,
            executed_immediate_tools=bool(results),
            should_continue=True,
        )

    @property
    def pending_red_calls(self) -> list[PendingAction]:
        """Return pending RED zone actions."""
        return list(self._pending_red)

    def clear_pending(self) -> None:
        """Clear pending RED zone calls."""
        self._pending_red.clear()
        self._last_invocation_id = None
        self._pending_continuation = None
        self._authorized_grants = {}

    def consume_pending(self, action_id: str) -> None:
        """Remove one decided confirmation while preserving the rest."""
        self._pending_red = [
            action for action in self._pending_red if action.action_id != action_id
        ]
        if not self._pending_red:
            self._last_invocation_id = None
            self._pending_continuation = None
