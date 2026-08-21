"""Shared fakes and SSE helpers for the MCP test modules.

FakeCredentialClient and FakeDBProxyIPC stand in for the credential and
IPC clients; _FakeMCPAdminClient and _FakeMCPOrchestratorClient stub the
admin and orchestrator clients; the SSE helpers drive the MCP server
over a Starlette test client.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.ipc.models import DBOperation, DBResult

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of a plaintext token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FakeCredentialClient:
    """Mock CredentialClient for agent-token tests."""

    def __init__(self) -> None:
        """Initialize empty integration/created/deleted records."""
        self._integrations: list[dict[str, Any]] = []
        self._created: list[dict[str, Any]] = []
        self._deleted: list[str] = []

    async def list_integrations(self) -> list[dict[str, Any]]:
        """Return a copy of the stored integrations."""
        return list(self._integrations)

    async def create_integration(
        self,
        name: str,
        integration_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Store one integration and return its new id."""
        entry = {
            "id": f"int-{len(self._integrations)}",
            "name": name,
            "service_type": integration_type,
            "enabled": True,
            "config": config,
            "created_at": "2025-01-01T00:00:00",
        }
        self._integrations.append(entry)
        self._created.append(entry)
        return {"integration_id": entry["id"]}

    async def delete_integration(self, name: str) -> dict[str, Any]:
        """Delete every integration matching the name."""
        self._integrations = [i for i in self._integrations if i.get("name") != name]
        self._deleted.append(name)
        return {"deleted": True}


class FakeDBProxyIPC:
    """Mock IPC client that returns preset DBResult responses."""

    def __init__(self) -> None:
        """Initialize empty response and sent-operation queues."""
        self._responses: list[DBResult] = []
        self._sent: list[DBOperation] = []

    def queue_response(self, result: DBResult) -> None:
        """Queue one DBResult to return from the next call."""
        self._responses.append(result)

    async def send_and_receive(self, message: object) -> object:
        """Record the message and return the next queued response."""
        self._sent.append(message)
        if not self._responses:
            raise RuntimeError("No response queued")
        return self._responses.pop(0)

    @property
    def sent_operations(self) -> list[DBOperation]:
        """Return all recorded sent operations."""
        return self._sent


# ──────────────────────────────────────────────────────────────────────
# Fakes for integration tests
# ──────────────────────────────────────────────────────────────────────


class _FakeMCPAdminClient:
    """Minimal admin client stub for the MCP integration test."""

    def __init__(self) -> None:
        self.searched: list[dict[str, Any]] = []
        self._entries: list[dict[str, Any]] = [
            {
                "id": f"msg-{i}",
                "role": "user",
                "content": f"entry {i}",
                "conversation_id": "conv-0",
                "created_at": f"2025-01-0{i + 1}T00:00:00",
                "meta": None,
                "table": "message",
            }
            for i in range(5)
        ]
        self._stats = {"messages": 5, "records": 3, "summaries": 2}

    async def fulltext_search(
        self,
        query: str,
        table: str = "messages",
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.searched.append(
            {"query": query, "table": table, "limit": limit, "offset": offset}
        )
        if "nomatch" in query.lower():
            return []
        return [
            {
                "id": f"id-{table}-1",
                "rowid": 1,
                "summary": f"hit:{table}",
                "created_at": "2026-08-01T00:00:00Z",
            }
        ]

    async def find_graph_entry_points(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        self.graph_searched = True
        if "nomatch" in query.lower():
            return []
        return [
            {
                "id": "assert-1",
                "kind": "assertion",
                "assertion_text": f"graph:{query}",
            }
        ]

    async def mcp_memory_recent(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return list(self._entries[offset : offset + limit])

    async def mcp_memory_get(
        self, entry_id: str, table: str = "messages"
    ) -> dict[str, Any] | None:
        for e in self._entries:
            if e["id"] == entry_id:
                return e
        return None

    async def mcp_memory_stats(self) -> dict[str, Any]:
        return {"total": sum(self._stats.values()), "by_type": dict(self._stats)}

    async def count_memory_entries(self) -> dict[str, int]:
        return dict(self._stats)

    async def list_messages(
        self, limit: int = 50, offset: int = 0, conversation_id: str | None = None
    ) -> list[dict[str, Any]]:
        entries = self._entries
        if conversation_id:
            entries = [
                e for e in entries if e.get("conversation_id") == conversation_id
            ]
        return entries[offset : offset + limit]

    async def list_records(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return []

    async def list_summaries_full(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return []

    async def _send_list_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if operation == "list_messages":
            cid = parameters.get("conversation_id") if parameters else None
            lim = (parameters or {}).get("limit", 50)
            off = (parameters or {}).get("offset", 0)
            return await self.list_messages(lim, off, cid)
        if operation == "list_records":
            return await self.list_records()
        if operation == "list_summaries_full":
            return await self.list_summaries_full()
        if operation == "mcp_memory_recent":
            lim = (parameters or {}).get("limit", 50)
            off = (parameters or {}).get("offset", 0)
            return await self.mcp_memory_recent(lim, off)
        return []

    async def _send_optional_dict_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if operation == "mcp_memory_get":
            eid = parameters.get("entry_id") if parameters else ""
            tbl = parameters.get("table", "messages") if parameters else "messages"
            return await self.mcp_memory_get(eid, tbl)
        return None

    async def send_operation(
        self, operation: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if operation == "mcp_memory_stats":
            return await self.mcp_memory_stats()
        if operation == "count_memory_entries":
            return await self.count_memory_entries()
        return {}


class _FakeMCPOrchestratorClient:
    """Minimal orchestrator client stub for the MCP integration test."""

    def __init__(self) -> None:
        self.remembered: list[dict[str, Any]] = []

    async def mcp_memory_remember(
        self,
        *,
        content: str,
        channel: str,
        agent_id: str | None = None,
    ):

        self.remembered.append({"content": content, "channel": channel})
        return SimpleNamespace(
            stored=True,
            message_id="msg-fake",
            conversation_id="conv-fake",
            reason="",
        )

    async def mcp_ask(
        self,
        *,
        question: str,
        agent_id: str | None = None,
    ):

        return SimpleNamespace(
            answer="Fake answer",
            conversation_id="conv-fake",
            session_id="session-fake",
            error="",
        )


# ──────────────────────────────────────────────────────────────────────
# SSE helpers (for integration test over HTTP)
# ──────────────────────────────────────────────────────────────────────


def _sse_events(text: str) -> list[dict[str, Any]]:
    """Parse ``data:`` JSON payloads out of an SSE response body."""
    return [
        json.loads(line[len("data:") :].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _handshake(client: object) -> str:
    """Run initialize + initialized and return the session id."""
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    response = client.post("/mcp/", json=init, headers=_MCP_HEADERS)
    session_id = response.headers.get("mcp-session-id")
    client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**_MCP_HEADERS, "mcp-session-id": session_id},
    )
    return session_id


def _call_tool(
    client: object,
    session_id: str,
    name: str,
    arguments: dict[str, Any],
    auth: str | None,
) -> dict[str, Any]:
    """Call one tool and return the parsed JSON-RPC result object."""
    headers = {**_MCP_HEADERS, "mcp-session-id": session_id}
    if auth is not None:
        headers["Authorization"] = auth
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    response = client.post("/mcp/", json=body, headers=headers)
    events = _sse_events(response.text)
    return events[-1]["result"]
