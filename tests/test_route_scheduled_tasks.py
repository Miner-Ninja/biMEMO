"""Behaviour contract tests for the scheduled-task admin routes (F27).

The routes validate the schedule and tool allowlist before persisting:
a parse error or an unauthorized tool is a 422, an empty update body is a
422, and the persisted payload carries the parsed schedule kind, the
recomputed next run in the user's timezone, and the JSON-serialized tool
allowlist. Deletion is a 204 and run-history limits are clamped to 1..100.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from backend.models.requests import (
    ScheduledTaskCreateRequest,
    ScheduledTaskUpdateRequest,
)
from backend.processes.fastapi_app.routes.scheduled_tasks import (
    create_scheduled_task,
    delete_scheduled_task,
    list_available_tools,
    list_scheduled_tasks,
    list_task_runs,
    update_scheduled_task,
)


VALID_BODY = ScheduledTaskCreateRequest(
    name="weekly digest",
    task_type="agent_task",
    prompt="Summarize the week",
    schedule="30m",
    allowed_tools=["search"],
)

DEFAULT_PROFILE = {"profile": {"timezone": "Europe/Moscow"}}


class _FakeAdmin:
    """Socket B admin client stand-in recording every call."""

    def __init__(self, profile: Any = None) -> None:
        self.profile = DEFAULT_PROFILE if profile is None else profile
        self.calls: list[tuple[str, dict]] = []

    async def list_scheduled_tasks(self, status: str = "all"):
        """Return the canned task list for any filter."""
        self.calls.append(("list", {"status": status}))
        return {"tasks": [{"id": "t1"}]}

    async def create_scheduled_task(self, payload: dict) -> dict:
        """Record the persisted payload and echo a task id."""
        self.calls.append(("create", payload))
        return {"task_id": "t1", "next_run_at": payload["next_run_at"]}

    async def update_scheduled_task(self, params: dict) -> None:
        """Record the update parameters."""
        self.calls.append(("update", params))

    async def delete_scheduled_task(self, task_id: str) -> None:
        """Record the deletion."""
        self.calls.append(("delete", {"task_id": task_id}))

    async def list_task_runs(self, task_id: str, limit: int = 20) -> dict:
        """Record the clamped limit lookup."""
        self.calls.append(("runs", {"task_id": task_id, "limit": limit}))
        return {"runs": []}

    async def get_user_profile(self) -> Any:
        """Return the profile, or raise when the profile is an exception."""
        if isinstance(self.profile, Exception):
            raise self.profile
        return self.profile


class _FakeOrchestrator:
    """Runtime registry client stand-in for tool authorization."""

    def __init__(self, unauthorized: set[str] | None = None) -> None:
        self.unauthorized = unauthorized or set()

    async def authorize_scheduled_tools(self, tools: list[str]) -> list[str]:
        """Return the tools, or reject a disallowed one."""
        for tool in tools:
            if tool in self.unauthorized:
                raise ValueError(f"tool not authorized: {tool}")
        return tools

    async def list_tool_catalog(self) -> list[dict]:
        """Return the canned catalog."""
        return [{"name": "search", "description": "Search"}]


class TestListScheduledTasks:
    """GET /admin/scheduled-tasks: status filter validation."""

    async def test_invalid_status_is_422(self):
        """A status outside the known set is rejected before any lookup."""
        with pytest.raises(HTTPException) as excinfo:
            await list_scheduled_tasks(status="bogus", claims=None, admin=_FakeAdmin())
        assert excinfo.value.status_code == 422

    async def test_valid_status_is_forwarded(self):
        """The filter is passed to the admin client unchanged."""
        admin = _FakeAdmin()
        result = await list_scheduled_tasks(status="paused", claims=None, admin=admin)
        assert result == {"tasks": [{"id": "t1"}]}
        assert admin.calls == [("list", {"status": "paused"})]


class TestCreateScheduledTask:
    """POST /admin/scheduled-tasks: validation, payload, and 201."""

    async def test_invalid_schedule_is_422(self):
        """A schedule the parser rejects is a 422 with the parser's reason."""
        body = VALID_BODY.model_copy(update={"schedule": "not a schedule"})
        with pytest.raises(HTTPException) as excinfo:
            await create_scheduled_task(
                body,
                claims=None,
                admin=_FakeAdmin(),
                orchestrator=_FakeOrchestrator(),
            )
        assert excinfo.value.status_code == 422

    async def test_unauthorized_tool_is_422(self):
        """A tool the registry does not allow is a 422; nothing is stored."""
        body = VALID_BODY.model_copy(update={"allowed_tools": ["dangerous"]})
        admin = _FakeAdmin()
        orchestrator = _FakeOrchestrator(unauthorized={"dangerous"})
        with pytest.raises(HTTPException) as excinfo:
            await create_scheduled_task(
                body,
                claims=None,
                admin=admin,
                orchestrator=orchestrator,
            )
        assert excinfo.value.status_code == 422
        assert admin.calls == []

    async def test_happy_path_payload_and_response(self):
        """The persisted payload carries the parsed schedule fields.

        The timezone comes from the profile, the tool list is JSON
        serialized, and the response is a 201 whose Location header
        points at the new task.
        """
        admin = _FakeAdmin()
        response = await create_scheduled_task(
            VALID_BODY,
            claims=None,
            admin=admin,
            orchestrator=_FakeOrchestrator(),
        )
        assert response.status_code == 201
        assert response.headers["Location"] == "/admin/scheduled-tasks/t1"
        ((_, payload),) = admin.calls
        assert payload["schedule_kind"] == "interval"
        assert payload["schedule_expr"] == "30m"
        assert payload["timezone"] == "Europe/Moscow"
        assert payload["allowed_tools"] == '["search"]'
        assert payload["name"] == "weekly digest"
        assert payload["next_run_at"]

    async def test_profile_failure_falls_back_to_utc(self):
        """A failing profile lookup must not fail the create: UTC applies."""
        admin = _FakeAdmin(profile=RuntimeError("db down"))
        response = await create_scheduled_task(
            VALID_BODY,
            claims=None,
            admin=admin,
            orchestrator=_FakeOrchestrator(),
        )
        assert response.status_code == 201
        _, payload = admin.calls[0]
        assert payload["timezone"] == "UTC"


class TestUpdateScheduledTask:
    """PATCH /admin/scheduled-tasks/{task_id}: partial updates."""

    async def test_empty_update_is_422(self):
        """An update with no fields is a 422, not a no-op write."""
        with pytest.raises(HTTPException) as excinfo:
            await update_scheduled_task(
                "t1",
                ScheduledTaskUpdateRequest(),
                claims=None,
                admin=_FakeAdmin(),
                orchestrator=_FakeOrchestrator(),
            )
        assert excinfo.value.status_code == 422
        assert "No fields" in excinfo.value.detail

    async def test_status_only_update_is_minimal(self):
        """Only the provided field plus the task id are written."""
        admin = _FakeAdmin()
        result = await update_scheduled_task(
            "t1",
            ScheduledTaskUpdateRequest(status="paused"),
            claims=None,
            admin=admin,
            orchestrator=_FakeOrchestrator(),
        )
        assert result == {"ok": True}
        assert admin.calls == [("update", {"task_id": "t1", "status": "paused"})]

    async def test_schedule_update_recomputes_next_run(self):
        """A changed schedule re-parses and recomputes the next run.

        The timezone is refreshed from the profile.
        """
        admin = _FakeAdmin()
        await update_scheduled_task(
            "t1",
            ScheduledTaskUpdateRequest(schedule="1h"),
            claims=None,
            admin=admin,
            orchestrator=_FakeOrchestrator(),
        )
        ((_, params),) = admin.calls
        assert params["schedule_kind"] == "interval"
        assert params["schedule_expr"] == "1h"
        assert params["timezone"] == "Europe/Moscow"
        assert params["next_run_at"]

    async def test_invalid_schedule_in_update_is_422(self):
        """An unparsable schedule in the update body is a 422."""
        with pytest.raises(HTTPException) as excinfo:
            await update_scheduled_task(
                "t1",
                ScheduledTaskUpdateRequest(schedule="nonsense"),
                claims=None,
                admin=_FakeAdmin(),
                orchestrator=_FakeOrchestrator(),
            )
        assert excinfo.value.status_code == 422


class TestDeleteScheduledTask:
    """DELETE /admin/scheduled-tasks/{task_id}: a 204 on success."""

    async def test_returns_204(self):
        """The delete is forwarded to the admin client and returns 204."""
        admin = _FakeAdmin()
        response = await delete_scheduled_task("t1", claims=None, admin=admin)
        assert response.status_code == 204
        assert admin.calls == [("delete", {"task_id": "t1"})]


class TestListTaskRuns:
    """GET /admin/scheduled-tasks/{task_id}/runs: limit clamping."""

    async def test_limit_below_one_is_clamped_to_one(self):
        """A non-positive limit becomes one."""
        admin = _FakeAdmin()
        await list_task_runs("t1", limit=0, claims=None, admin=admin)
        assert admin.calls[0][1]["limit"] == 1

    async def test_limit_above_100_is_clamped_to_100(self):
        """An oversized limit is capped at one hundred."""
        admin = _FakeAdmin()
        await list_task_runs("t1", limit=500, claims=None, admin=admin)
        assert admin.calls[0][1]["limit"] == 100

    async def test_list_result_is_normalized(self):
        """The dict result is returned under the ``runs`` key."""
        admin = _FakeAdmin()
        result = await list_task_runs("t1", limit=20, claims=None, admin=admin)
        assert result == {"runs": []}


class TestListAvailableTools:
    """GET /admin/tools: the orchestrator's tool catalog passthrough."""

    async def test_returns_catalog(self):
        """The catalog is returned under the ``tools`` key."""
        result = await list_available_tools(
            claims=None, orchestrator=_FakeOrchestrator()
        )
        assert result == {"tools": [{"name": "search", "description": "Search"}]}
