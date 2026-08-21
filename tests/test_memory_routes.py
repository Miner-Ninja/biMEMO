"""Integration tests for memory timeline, quality, and diagnostics routes."""

from tests.llm_config_fakes import (
    FakeAdminClient,
    _authed_client_with_csrf,
    _build_test_app,
)


class TestMemoryTimeline:
    """Integration tests for merged memory timeline pagination."""

    def test_timeline_applies_offset_after_merging_streams(self) -> None:
        """Timeline pagination must apply after merging all memory streams."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/timeline?limit=2&offset=1&sort=desc")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        entries = body["entries"]
        assert [entry["id"] for entry in entries] == ["o1", "r1"]
        assert body["total_count"] == 6

    def test_quality_route_returns_memory_health_report(self) -> None:
        """Memory quality route should expose diagnostic quality metrics."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/quality")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "quality_score" in body
        assert body["records"]["total"] == 2
        assert body["summaries"]["status_breakdown"]["orphaned"] == 1
        assert body["records"]["with_provenance"] == 1

    def test_messages_route_supports_conversation_filter(self) -> None:
        """Messages route should return raw entries for one conversation."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/messages?conversation_id=conversation-1")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "messages": [
                {
                    "id": "m1",
                    "content": "message-1",
                    "created_at": "2026-03-12T10:00:03Z",
                    "role": "user",
                    "meta": "",
                    "conversation_id": "conversation-1",
                }
            ]
        }

    def test_chat_conversations_route_returns_server_backed_list(self) -> None:
        """Return stored conversations from the database chat list route."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/chat/conversations")

        assert resp.status_code == 200, resp.text
        default_settings = {
            "show_thinking": False,
            "show_suggestions": True,
            "thinking_enabled": True,
        }
        assert resp.json() == [
            {
                "id": "conversation-2",
                "title": "Conversation two",
                "bridge_provider": None,
                "settings": default_settings,
                "processing_status": "idle",
                "processing_stage": "",
                "processing_started_at": None,
                "created_at": "2026-03-12T10:00:00Z",
                "updated_at": "2026-03-12T10:00:00Z",
            },
            {
                "id": "conversation-1",
                "title": "Conversation one",
                "bridge_provider": None,
                "settings": default_settings,
                "processing_status": "idle",
                "processing_stage": "",
                "processing_started_at": None,
                "created_at": "2026-03-12T10:00:03Z",
                "updated_at": "2026-03-12T10:00:03Z",
            },
        ]

    def test_chat_conversation_messages_route_returns_ordered_history(self) -> None:
        """Return one persisted conversation history in ascending time order."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/chat/conversations/conversation-1/messages")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "messages": [
                {
                    "id": "m1",
                    "content": "message-1",
                    "created_at": "2026-03-12T10:00:03Z",
                    "role": "user",
                    "meta": "",
                    "conversation_id": "conversation-1",
                    "structured_content": None,
                    "used_tools": [],
                    "pending_confirmations": [],
                }
            ],
            "next_cursor": None,
            "has_more": False,
        }

    def test_chat_create_conversation_route_persists_server_side_chat(self) -> None:
        """Chat create conversation route should create one persisted conversation."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, csrf_token = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.post(
            "/chat/conversations",
            headers={"X-CSRF-Token": csrf_token},
            json={"title": "Server chat"},
        )

        assert resp.status_code == 201, resp.text
        assert resp.json() == {"conversation_id": "conversation-3"}
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_kwargs = (
            app.state.orchestrator_client.publish_conversation_event.await_args.kwargs
        )
        assert publish_kwargs["conversation_id"] == "conversation-3"
        assert publish_kwargs["event_type"].value == "conversation_created"
        assert publish_kwargs["data"]["conversation_id"] == "conversation-3"
        assert publish_kwargs["data"]["title"] == "Server chat"
        assert publish_kwargs["data"]["bridge_provider"] is None

    def test_chat_update_conversation_route_renames_server_side_chat(self) -> None:
        """Chat update conversation route should rename one persisted chat."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, csrf_token = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.put(
            "/chat/conversations/conversation-1",
            headers={"X-CSRF-Token": csrf_token},
            json={"title": "Renamed chat"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "id": "conversation-1",
            "title": "Renamed chat",
            "bridge_provider": None,
            "settings": {
                "show_thinking": False,
                "show_suggestions": True,
                "thinking_enabled": True,
            },
            "processing_status": "idle",
            "processing_stage": "",
            "processing_started_at": None,
            "created_at": "2026-03-12T10:00:03Z",
            "updated_at": "2026-03-12T10:00:09Z",
        }
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_kwargs = (
            app.state.orchestrator_client.publish_conversation_event.await_args.kwargs
        )
        assert publish_kwargs["conversation_id"] == "conversation-1"
        assert publish_kwargs["event_type"].value == "conversation_updated"
        assert publish_kwargs["data"]["title"] == "Renamed chat"
        assert publish_kwargs["data"]["bridge_provider"] is None

    def test_chat_delete_conversation_route_removes_server_side_chat(self) -> None:
        """Chat delete conversation route should remove one persisted chat."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, csrf_token = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.delete(
            "/chat/conversations/conversation-1",
            headers={"X-CSRF-Token": csrf_token},
        )

        assert resp.status_code == 204
        assert resp.content == b""
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_kwargs = (
            app.state.orchestrator_client.publish_conversation_event.await_args.kwargs
        )
        assert publish_kwargs["conversation_id"] == "conversation-1"
        assert publish_kwargs["event_type"].value == "conversation_deleted"
        assert publish_kwargs["data"]["conversation_id"] == "conversation-1"

    def test_observations_route_returns_full_records(self) -> None:
        """Records route should expose provenance-rich record data."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/records?limit=1")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "records": [
                {
                    "id": "o1",
                    "summary": "record-1",
                    "created_at": "2026-03-12T10:00:02Z",
                    "key_facts": ["fact-1"],
                    "action_items": ["action-1"],
                    "memory_kinds": ["preference"],
                    "conversation_id": "conversation-1",
                    "source_message_ids": ["m1"],
                }
            ]
        }

    def test_route_diagnostics_route_returns_structured_records(self) -> None:
        """Route diagnostics route should expose stored routing decisions."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get(
            "/admin/memory/route-diagnostics"
            "?limit=1&mismatch_only=true&final_intent=user_recall_question"
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "diagnostics": [
                {
                    "id": 1,
                    "created_at": "2026-03-12T10:00:04Z",
                    "session_id": "session-1",
                    "conversation_id": "conversation-1",
                    "message_id": "m1",
                    "channel": "web",
                    "initial_route": {
                        "intent": "non_memory_question",
                        "confidence": "none",
                        "role_filter": None,
                        "source": "signal",
                        "signal_groups": [],
                    },
                    "fallback": {
                        "attempted": True,
                        "reason": "unsupported_language_question_shape",
                        "intent": "user_recall_question",
                        "confidence": "high",
                        "role_filter": "user",
                        "source": "semantic_fallback",
                    },
                    "final_route": {
                        "intent": "user_recall_question",
                        "confidence": "high",
                        "role_filter": "user",
                        "source": "semantic_fallback",
                        "signal_groups": ["user_recall_signals"],
                    },
                    "route_mismatch": True,
                    "mismatch_label": "intent_changed",
                    "used_long_term_mode": False,
                }
            ]
        }

    def test_route_diagnostics_summary_route_returns_aggregates(self) -> None:
        """Route diagnostics summary route should expose aggregate counts."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/route-diagnostics/summary")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "totals": {
                "total_rows": 1,
                "fallback_rows": 1,
                "mismatch_rows": 1,
                "long_term_rows": 0,
            },
            "recent_hours": None,
            "mismatch_labels": [
                {"label": "intent_changed", "count": 1},
            ],
            "final_intents": [
                {"intent": "user_recall_question", "count": 1},
            ],
            "fallback_reasons": [
                {
                    "reason": "unsupported_language_question_shape",
                    "count": 1,
                }
            ],
        }

    def test_route_diagnostics_summary_route_accepts_recent_hours(self) -> None:
        """Route diagnostics summary route should pass the recent-hours filter."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/route-diagnostics/summary?recent_hours=24")

        assert resp.status_code == 200, resp.text
        assert resp.json()["recent_hours"] == 24

    def test_dialog_recorder_trace_route_returns_record_entries(self) -> None:
        """Recorder trace route exposes recent record-processing entries."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/dialog-recorder-trace?limit=1")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "entries": [
                {
                    "timestamp": "2026-03-15T12:00:00Z",
                    "decision": "record_stored_by_llm_memory_judgment",
                    "conversation_id": "conversation-1",
                    "session_id": "session-1",
                    "messages": ["User: hello", "Assistant: hi"],
                    "source_message_ids": ["m1", "m2"],
                    "message_count": 2,
                }
            ]
        }
