"""Integration tests for audit log entries on security events."""

from starlette.testclient import TestClient

from tests.llm_config_fakes import FakeCredentialClient, _authed_client, _build_test_app


class TestSecurityAuditLogging:
    """Integration tests for audit log entries on security events."""

    def test_failed_login_generates_audit_entry(self) -> None:
        """POST /auth/login with wrong password -> audit_log has auth_failure."""
        app, _token, _session_manager, _signing_client, audit_log = _build_test_app()
        client = TestClient(app)

        resp = client.post(
            "/auth/login",
            json={"password": "wrong-password"},
        )
        assert resp.status_code == 401

        auth_failures = [e for e in audit_log if e["event_type"] == "auth_failure"]
        assert len(auth_failures) >= 1
        assert auth_failures[0]["tool_name"] == "login"

    def test_successful_login_generates_audit_entry(self) -> None:
        """POST /auth/login with correct password -> audit_log has auth_success."""
        app, _token, _session_manager, _signing_client, audit_log = _build_test_app()
        client = TestClient(app)

        resp = client.post(
            "/auth/login",
            json={"password": "correct-password"},
        )
        assert resp.status_code == 200

        auth_successes = [e for e in audit_log if e["event_type"] == "auth_success"]
        assert len(auth_successes) >= 1
        assert auth_successes[0]["tool_name"] == "login"

    def test_csrf_rejection_generates_audit_entry(self) -> None:
        """POST /chat without CSRF token -> audit_log has csrf_rejected."""
        app, token, _session_manager, _signing_client, audit_log = _build_test_app()
        client = _authed_client(app, token)

        # First get a CSRF token so the session has one registered,
        # but then send a request WITHOUT it
        client.get("/csrf-token")

        resp = client.post(
            "/chat",
            json={"message": "hello"},
            # No X-CSRF-Token header -> should be rejected
        )
        assert resp.status_code == 403

        csrf_entries = [e for e in audit_log if e["event_type"] == "csrf_rejected"]
        assert len(csrf_entries) >= 1

    def test_rate_limit_generates_audit_entry(self) -> None:
        """Exhaust rate limit -> audit_log has rate_limited entry."""
        app, token, _session_manager, _signing_client, audit_log = _build_test_app()
        limiter = app.state.rate_limiter

        # Exhaust the rate limit for the "health" endpoint (GET, no CSRF needed)
        for _ in range(60):
            limiter.record("health")

        client = _authed_client(app, token)
        resp = client.get("/health")
        assert resp.status_code == 429

        rate_entries = [e for e in audit_log if e["event_type"] == "rate_limited"]
        assert len(rate_entries) >= 1

    def test_multiple_security_events_accumulate(self) -> None:
        """Multiple security events all appear in the audit log."""
        app, token, _session_manager, _signing_client, audit_log = _build_test_app()
        app.state.credential_client = FakeCredentialClient()

        # 1. Failed login
        unauthenticated_client = TestClient(app)
        unauthenticated_client.post(
            "/auth/login",
            json={"password": "wrong-password"},
        )

        # 2. CSRF rejection (authed but no CSRF)
        client = _authed_client(app, token)
        client.get("/csrf-token")  # Register CSRF for session
        client.post("/chat", json={"message": "hi"})  # No CSRF header

        # 3. Rate limit
        limiter = app.state.rate_limiter
        for _ in range(60):
            limiter.record("health")
        TestClient(app).get("/health")

        event_types = {e["event_type"] for e in audit_log}
        assert "auth_failure" in event_types
        assert "csrf_rejected" in event_types
        assert "rate_limited" in event_types

    def test_auth_failure_does_not_leak_password(self) -> None:
        """Verify audit entries never contain the submitted password."""
        app, _, _, _, audit_log = _build_test_app()
        client = TestClient(app)

        secret_password = "super-secret-p@ssw0rd-12345"
        client.post(
            "/auth/login",
            json={"password": secret_password},
        )

        for entry in audit_log:
            for value in entry.values():
                if isinstance(value, str):
                    assert secret_password not in value, (
                        f"Password leaked into audit entry: {entry}"
                    )
