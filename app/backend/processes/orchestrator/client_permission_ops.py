"""Assistant permission DB Proxy operations.

Provides module-level async functions that wrap DBProxyClient.send_operation
for trusted project roots, capability grants, agent run events,
confirmations, pending actions, and runtime state pruning. Each function
receives a DBProxyClient instance as its first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def list_project_roots(client: DBProxyClient) -> list[dict[str, Any]]:
    """Return configured project roots for assistant actions.

    Args:
        client: DB Proxy client instance.

    Returns:
        Project root rows.
    """
    result = await client.send_operation("list_project_roots")
    return result.get("project_roots", [])


async def add_project_root(
    client: DBProxyClient,
    *,
    name: str,
    root_path: str,
) -> dict[str, Any] | None:
    """Create or update one trusted project root.

    Args:
        client: DB Proxy client instance.
        name: Root display name.
        root_path: Absolute path of the root.

    Returns:
        Persisted project root row, if any.
    """
    result = await client.send_operation(
        "add_project_root",
        {"name": name, "root_path": root_path},
    )
    return result.get("project_root")


async def remove_project_root(client: DBProxyClient, root_id: str) -> bool:
    """Remove one trusted project root.

    Args:
        client: DB Proxy client instance.
        root_id: Root to remove.

    Returns:
        True if a row was removed.
    """
    result = await client.send_operation("remove_project_root", {"id": root_id})
    return bool(result.get("removed"))


async def list_capability_grants(
    client: DBProxyClient,
    *,
    scope_type: str | None = None,
    scope_value: str | None = None,
    capability: str | None = None,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    """Return persisted assistant action grants.

    Args:
        client: DB Proxy client instance.
        scope_type: Optional scope type filter.
        scope_value: Optional scope value filter.
        capability: Optional capability filter.
        channel: Optional channel filter.

    Returns:
        Matching grant rows.
    """
    params = {
        key: value
        for key, value in {
            "scope_type": scope_type,
            "scope_value": scope_value,
            "capability": capability,
            "channel": channel,
        }.items()
        if value is not None
    }
    result = await client.send_operation("list_capability_grants", params)
    return result.get("grants", [])


async def create_capability_grant(
    client: DBProxyClient,
    *,
    scope_type: str,
    scope_value: str,
    capability: str,
    decision: str,
    policy_revision: str,
    channel: str = "",
    scope_json: dict[str, Any] | None = None,
    scope_hash: str = "",
    origin_confirmation_id: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any] | None:
    """Persist one assistant action grant.

    Args:
        client: DB Proxy client instance.
        scope_type: Scope type of the grant.
        scope_value: Scope value of the grant.
        capability: Authorized capability.
        decision: Decision stored with the grant.
        policy_revision: Policy revision the grant was issued under.
        channel: Channel the grant applies to.
        scope_json: Full scope JSON document.
        scope_hash: Hash of the scope JSON document.
        origin_confirmation_id: Confirmation that produced the grant.
        expires_at: Expiry time (ISO-8601), or None for no expiry.

    Returns:
        Persisted grant row, if any.
    """
    result = await client.send_operation(
        "create_capability_grant",
        {
            "scope_type": scope_type,
            "scope_value": scope_value,
            "capability": capability,
            "decision": decision,
            "channel": channel,
            "scope_json": scope_json or {},
            "scope_hash": scope_hash,
            "policy_revision": policy_revision,
            "origin_confirmation_id": origin_confirmation_id,
            "expires_at": expires_at,
        },
    )
    return result.get("grant")


async def lookup_capability_grant(
    client: DBProxyClient,
    *,
    scope_type: str,
    scope_value: str,
    capability: str,
    policy_revision: str,
    channel: str = "",
    scope_hash: str = "",
) -> dict[str, Any] | None:
    """Return the best persisted grant for one action scope.

    Args:
        client: DB Proxy client instance.
        scope_type: Scope type of the action.
        scope_value: Scope value of the action.
        capability: Capability being checked.
        policy_revision: Active policy revision.
        channel: Channel of the action.
        scope_hash: Hash of the action scope JSON.

    Returns:
        Best matching grant row, if any.
    """
    result = await client.send_operation(
        "lookup_capability_grant",
        {
            "scope_type": scope_type,
            "scope_value": scope_value,
            "capability": capability,
            "channel": channel,
            "scope_hash": scope_hash,
            "policy_revision": policy_revision,
        },
    )
    return result.get("grant")


async def authorize_agent_invocation(
    client: DBProxyClient,
    *,
    turn_id: str,
    invocation_id: str,
    idempotency_key: str,
    capability: str,
    channel: str,
    scope_type: str,
    scope_value: str,
    scope_json: dict[str, Any],
    scope_hash: str,
    policy_revision: str,
    canonical_args_hash: str,
    confirmation_id: str | None = None,
    create_reusable_grant: bool = False,
    grant_expires_at: str | None = None,
) -> dict[str, Any]:
    """Atomically authorize and claim one structured invocation.

    Args:
        client: DB Proxy client instance.
        turn_id: Structured turn containing the invocation.
        invocation_id: Stable invocation identifier.
        idempotency_key: Idempotency key for the claim.
        capability: Authorized capability.
        channel: Channel the invocation runs on.
        scope_type: Authorization scope type.
        scope_value: Authorization scope value.
        scope_json: Full authorization scope JSON.
        scope_hash: Hash of the authorization scope JSON.
        policy_revision: Policy revision authorizing the action.
        canonical_args_hash: Hash of the canonical tool arguments.
        confirmation_id: Confirmation that authorized the action, if any.
        create_reusable_grant: Whether to persist a reusable grant.
        grant_expires_at: Expiry for the reusable grant, if any.

    Returns:
        Authorization result.
    """
    parameters: dict[str, Any] = {
        "turn_id": turn_id,
        "invocation_id": invocation_id,
        "idempotency_key": idempotency_key,
        "capability": capability,
        "channel": channel,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "scope_json": scope_json,
        "scope_hash": scope_hash,
        "policy_revision": policy_revision,
        "canonical_args_hash": canonical_args_hash,
        "confirmation_id": confirmation_id,
        "create_reusable_grant": create_reusable_grant,
        "grant_expires_at": grant_expires_at,
    }
    return await client.send_operation("authorize_agent_invocation", parameters)


async def revoke_capability_grant(client: DBProxyClient, grant_id: str) -> bool:
    """Remove one persisted assistant action grant.

    Args:
        client: DB Proxy client instance.
        grant_id: Grant to revoke.

    Returns:
        True if the grant was revoked.
    """
    result = await client.send_operation("revoke_capability_grant", {"id": grant_id})
    return bool(result.get("revoked"))


async def expire_capability_grants(
    client: DBProxyClient,
    now: str | None = None,
) -> int:
    """Delete expired assistant action grants.

    Args:
        client: DB Proxy client instance.
        now: Reference time (ISO-8601); None uses the DB clock.

    Returns:
        Number of expired grants deleted.
    """
    params = {"now": now} if now is not None else {}
    result = await client.send_operation("expire_capability_grants", params)
    return int(result.get("expired", 0))


async def list_agent_run_events(
    client: DBProxyClient,
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    conversation_id: str | None = None,
    after_sequence: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return persisted runtime events for a run or conversation.

    Args:
        client: DB Proxy client instance.
        run_id: Optional run filter.
        request_id: Optional request filter.
        conversation_id: Optional conversation filter.
        after_sequence: Return events after this sequence number.
        limit: Maximum events returned.

    Returns:
        Runtime event rows.
    """
    result = await client.send_operation(
        "list_agent_run_events",
        {
            "run_id": run_id,
            "request_id": request_id,
            "conversation_id": conversation_id,
            "after_sequence": after_sequence,
            "limit": limit,
        },
    )
    events = result.get("events", [])
    return events if isinstance(events, list) else []


async def list_agent_confirmations(
    client: DBProxyClient,
    turn_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return every confirmation of one turn, any status, current first.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn whose confirmations are listed.
        limit: Maximum confirmations returned.

    Returns:
        Confirmation rows.
    """
    result = await client.send_operation(
        "list_agent_confirmations",
        {"turn_id": turn_id, "limit": limit},
    )
    confirmations = result.get("confirmations", [])
    return confirmations if isinstance(confirmations, list) else []


async def list_pending_actions(
    client: DBProxyClient,
    *,
    run_id: str | None = None,
    status: str | None = None,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    """Return persisted pending actions with optional filters.

    Args:
        client: DB Proxy client instance.
        run_id: Optional run filter.
        status: Optional status filter.
        channel: Optional channel filter.

    Returns:
        Pending action rows.
    """
    params = {
        key: value
        for key, value in {
            "run_id": run_id,
            "status": status,
            "channel": channel,
        }.items()
        if value is not None
    }
    result = await client.send_operation("list_pending_actions", params)
    return result.get("pending_actions", [])


async def prune_runtime_state(
    client: DBProxyClient,
    *,
    now: str | None = None,
    snapshot_retention_hours: int = 24,
    run_retention_days: int = 7,
) -> dict[str, int]:
    """Expire and delete stale short-lived assistant runtime rows.

    Args:
        client: DB Proxy client instance.
        now: Reference time (ISO-8601); None uses the DB clock.
        snapshot_retention_hours: Resume snapshot retention.
        run_retention_days: Agent run retention.

    Returns:
        Counts of expired and deleted rows by table.
    """
    params: dict[str, Any] = {
        "snapshot_retention_hours": snapshot_retention_hours,
        "run_retention_days": run_retention_days,
    }
    if now is not None:
        params["now"] = now
    result = await client.send_operation("prune_runtime_state", params)
    return {
        "expired_pending_actions": int(result.get("expired_pending_actions", 0)),
        "deleted_resume_snapshots": int(result.get("deleted_resume_snapshots", 0)),
        "deleted_agent_runs": int(result.get("deleted_agent_runs", 0)),
    }
