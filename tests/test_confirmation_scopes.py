"""Contract tests: confirmation scopes over the confirm chain (R3-R9)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.agent.tool_executor import ToolExecutor
from backend.ipc.models import ConfirmRequest as IPCConfirmRequest
from backend.models.requests import ConfirmRequest as HTTPConfirmRequest
from backend.permissions import ActionDecision, DecisionKind, domain_scope, scope_hash
from backend.tools.handlers.registration import confirmation_button_extras
from backend.trust.confirmation import ConfirmationManager


class TestScopeWire:
    """R3 — the scope field rides the existing confirm request."""

    def test_http_scope_accepts_the_three_values(self):
        """S3.1: every panel button value validates on the HTTP model."""
        for scope in ("once", "domain", "conversation"):
            body = HTTPConfirmRequest(action_id="a1", decision="approve", scope=scope)
            assert body.scope == scope

    def test_http_scope_defaults_to_once(self):
        """R3: an approval without a scope is an approval for once."""
        assert HTTPConfirmRequest(action_id="a1", decision="approve").scope == "once"

    def test_http_scope_rejects_unknown_values(self):
        """R3: any other scope value fails model validation."""
        with pytest.raises(ValidationError):
            HTTPConfirmRequest(action_id="a1", decision="approve", scope="forever")

    def test_ipc_scope_mirrors_the_http_contract(self):
        """S3.2: the IPC twin carries the same field and pattern."""
        request = IPCConfirmRequest(
            request_id="r1", action_id="a1", decision="approve", scope="domain"
        )
        assert request.scope == "domain"
        with pytest.raises(ValidationError):
            IPCConfirmRequest(
                request_id="r1", action_id="a1", decision="approve", scope="all"
            )


class TestServerSideHostDerivation:
    """R6 — the domain host comes from canonical parameters, never the client."""

    def test_host_lowercased_from_canonical_url(self):
        """S6.3: host is derived server-side from the canonical url argument."""
        prepared = SimpleNamespace(
            canonical_parameters={"url": "https://Example.COM/path?q=1"}
        )
        assert ToolExecutor.canonical_url_host(prepared) == "example.com"

    def test_no_url_argument_yields_none(self):
        """S6.4 precondition: non-URL tools have no derivable host."""
        prepared = SimpleNamespace(canonical_parameters={"command": "ls"})
        assert ToolExecutor.canonical_url_host(prepared) is None

    def test_unparsable_url_yields_none(self):
        """S6.5: an unparsable url argument fails closed to None."""
        prepared = SimpleNamespace(canonical_parameters={"url": "::::"})
        assert ToolExecutor.canonical_url_host(prepared) is None


def _executor_with_context() -> ToolExecutor:
    executor = ToolExecutor(MagicMock(), ConfirmationManager())
    executor.current_context = {
        "conversation_id": "conv-1",
        "channel": "web",
        "agent_run_id": "turn-1",
        "tool_policy_version": "registry-v1",
    }
    return executor


def _http_fetch_decision() -> ActionDecision:
    scope = {
        "conversation_id": "conv-1",
        "channel": "web",
        "tool_name": "http_fetch",
    }
    return ActionDecision(
        DecisionKind.ASK_ONCE,
        details={
            "permission_mode": "conversation_capability",
            "scope": scope,
            "scope_hash": scope_hash(scope),
            "policy_revision": "registry-v1",
            "read_only": True,
        },
    )


class TestDomainGrantCoordinates:
    """R6 — the domain grant binds conversation and host, hashed centrally."""

    def test_domain_coordinates_bind_host(self):
        """Domain grant coordinates bind the conversation and the call's host."""
        executor = _executor_with_context()
        coords = executor._grant_coordinates(
            _http_fetch_decision(), domain_host="example.com"
        )
        assert coords["scope_type"] == "domain"
        assert coords["scope_value"] == "conv-1:example.com"
        assert coords["scope"]["url_host"] == "example.com"
        expected = scope_hash(
            domain_scope(dict(_http_fetch_decision().details["scope"]), "example.com")
        )
        assert coords["scope_hash"] == expected

    def test_without_host_policy_coordinates_are_kept(self):
        """Without a host, coordinates stay at the conversation-capability scope."""
        executor = _executor_with_context()
        coords = executor._grant_coordinates(_http_fetch_decision())
        assert coords["scope_type"] == "conversation_capability"
        assert coords["scope_value"] == "conv-1"
        assert "url_host" not in coords["scope"]


class TestDomainGrantReuse:
    """R6 — reuse keys on the host of the CURRENT call's canonical URL."""

    def _executor_with_store(self, lookup_results: dict) -> ToolExecutor:
        executor = _executor_with_context()

        async def lookup_capability_grant(**kwargs):
            return lookup_results.get((kwargs["scope_type"], kwargs["scope_value"]))

        executor.turn_store = SimpleNamespace(
            lookup_capability_grant=AsyncMock(side_effect=lookup_capability_grant)
        )
        return executor

    def _decision(self) -> ActionDecision:
        return _http_fetch_decision()

    async def _partition(self, executor, url):
        from backend.agent.agent_loop_models import ToolCall

        call = ToolCall(id="c1", name="http_fetch", parameters={"url": url})
        executor._prepared_calls = {
            "c1": SimpleNamespace(canonical_parameters={"url": url})
        }
        executor._decide_tool_action = MagicMock(return_value=self._decision())
        executor._scheduled_task_preauthorized = MagicMock(return_value=False)
        return await executor._partition_structured_calls([call])

    @pytest.mark.asyncio
    async def test_same_host_promotes_without_confirmation(self):
        """S6.1: a grant for example.com covers another example.com URL."""
        executor = self._executor_with_store(
            {("domain", "conv-1:example.com"): {"decision": "allow", "id": "g1"}}
        )
        immediate, confirmation = await self._partition(
            executor, "https://example.com/b/c"
        )
        assert [call.name for call in immediate] == ["http_fetch"]
        assert confirmation == []
        assert executor._authorized_grants["c1"]["scope_type"] == "domain"

    @pytest.mark.asyncio
    async def test_other_host_requires_confirmation(self):
        """S6.2: the same grant never covers other.org."""
        executor = self._executor_with_store(
            {("domain", "conv-1:example.com"): {"decision": "allow", "id": "g1"}}
        )
        immediate, confirmation = await self._partition(executor, "https://other.org/")
        assert immediate == []
        assert [call.name for call in confirmation] == ["http_fetch"]

    @pytest.mark.asyncio
    async def test_missing_host_fails_closed(self):
        """S6.5: no parsable host — no domain lookup, normal confirmation."""
        executor = self._executor_with_store(
            {("domain", "conv-1:example.com"): {"decision": "allow", "id": "g1"}}
        )
        executor._prepared_calls = {"c1": SimpleNamespace(canonical_parameters={})}
        from backend.agent.agent_loop_models import ToolCall

        call = ToolCall(id="c1", name="http_fetch", parameters={})
        executor._decide_tool_action = MagicMock(return_value=self._decision())
        executor._scheduled_task_preauthorized = MagicMock(return_value=False)
        immediate, confirmation = await executor._partition_structured_calls([call])
        assert immediate == []
        assert [call.name for call in confirmation] == ["http_fetch"]


class TestButtonExtrasPayload:
    """R2/S2.4 — the panel's facts are computed server-side only."""

    def test_url_grant_capable_tool(self):
        """An http_fetch with a URL is grant-capable and reports the host."""
        extras = confirmation_button_extras(
            "http_fetch", {"url": "https://Example.COM/x"}
        )
        assert extras == {"grant_capable": True, "url_host": "example.com"}

    def test_per_invocation_tool(self):
        """execute_command has no domain grant and is reported as not grant-capable."""
        extras = confirmation_button_extras("execute_command", {"command": "ls"})
        assert extras == {"grant_capable": False, "url_host": None}

    def test_unknown_tool_fails_closed(self):
        """An unknown tool name fails closed: not grant-capable, no host."""
        extras = confirmation_button_extras("no_such_tool", {})
        assert extras == {"grant_capable": False, "url_host": None}


class TestDomainRedirectContainment:
    """R7/S7.1 — no request escapes the granted host."""

    @pytest.mark.asyncio
    async def test_cross_host_redirect_refused_before_request(self):
        """A 302 to another host fails BEFORE any request reaches it."""
        from backend.tools.validation import ValidationError
        from backend.workers import net_worker

        requested: list[str] = []

        class _Resp:
            status_code = 302
            content = b""
            text = ""

            def __init__(self) -> None:
                self.headers = {"location": "https://evil.com/steal"}

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                requested.append(url)
                return _Resp()

        original = net_worker.httpx.AsyncClient
        net_worker.httpx.AsyncClient = _Client
        try:
            with pytest.raises(ValidationError, match="granted domain"):
                await net_worker.execute_http_fetch(
                    "https://example.com/r", allowed_host="example.com"
                )
        finally:
            net_worker.httpx.AsyncClient = original
        assert requested == ["https://example.com/r"]

    @pytest.mark.asyncio
    async def test_initial_url_outside_grant_makes_no_request(self):
        """S6-adjacent: a granted call may not even start on a foreign host."""
        from backend.tools.validation import ValidationError
        from backend.workers import net_worker

        with pytest.raises(ValidationError, match="granted domain"):
            await net_worker.execute_http_fetch(
                "https://evil.com/x", allowed_host="example.com"
            )
