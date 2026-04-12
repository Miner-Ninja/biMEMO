"""Integration tests for the process-isolation architecture.

Tests cover:
- E2E flow through Supervisor → Orchestrator → DB Proxy
- Process isolation: Orchestrator doesn't hold SQLCipher key
- Process isolation: FastAPI holds only signing key
- DB Proxy Socket A rejects credential operations
- Crash recovery via supervisor restart policy
- IPC streaming: ChatRequest → StreamEvent → ChatResponse
- Docker network topology (static parse)

These tests use mocks for actual processes but verify the architecture
contracts, IPC protocols, and security boundaries.
"""

import asyncio
import inspect
from pathlib import Path

import pytest
from backend.ipc.models import (
    ChatRequest,
    ChatResponse,
    DBOperation,
    DBResult,
    StreamEvent,
    StructuredAssistantContent,
)


def _get_package_source(module: object) -> str:
    """Return concatenated source of all .py files in a package (or module).

    For packages (directories with __init__.py), reads all .py files recursively.
    For plain modules, falls back to inspect.getsource().
    """
    pkg_path = getattr(module, "__path__", None)
    if pkg_path is None:
        return inspect.getsource(module)

    parts: list[str] = []
    for directory in pkg_path:
        for py_file in sorted(Path(directory).rglob("*.py")):
            parts.append(py_file.read_text())
    return "\n".join(parts)


# ============================================================
# 17.1 End-to-end: supervisor → FastAPI → Orchestrator → DB Proxy
# ============================================================


class TestEndToEndFlow:
    """Verify message flow through the full IPC chain.

    Tests the contract: FastAPI sends ChatRequest via IPC → Orchestrator
    processes it → sends DBOperations to DB Proxy → returns ChatResponse.
    """

    def test_chat_request_serialization_roundtrip(self):
        """ChatRequest serializes and deserializes through IPC envelope."""
        request = ChatRequest(
            request_id="test-123",
            message="Hello",
            channel="web",
            session_id="sess-1",
            conversation_id="conv-1",
        )
        data = request.model_dump()
        restored = ChatRequest(**data)
        assert restored.request_id == "test-123"
        assert restored.message == "Hello"

    def test_db_operation_roundtrip(self):
        """DBOperation serializes through IPC with parameters."""
        operation = DBOperation(
            request_id="op-456",
            operation="save_message",
            parameters={"conversation_id": "c1", "role": "user", "content": "hi"},
        )
        data = operation.model_dump()
        restored = DBOperation(**data)
        assert restored.operation == "save_message"
        assert restored.parameters["content"] == "hi"

    def test_db_result_success(self):
        """DBResult carries success data."""
        result = DBResult(
            request_id="op-456",
            status="success",
            data={"rows_affected": 1},
        )
        assert result.status == "success"
        assert result.data["rows_affected"] == 1

    def test_db_result_error(self):
        """DBResult carries error message."""
        result = DBResult(
            request_id="op-456",
            status="error",
            error_message="Operation not allowed on channel-a",
        )
        assert result.status == "error"
        assert "not allowed" in result.error_message

    def test_chat_response_fields(self):
        """ChatResponse contains all expected fields."""
        response = ChatResponse(
            request_id="test-123",
            structured_content=StructuredAssistantContent(
                answer="Hello! How can I help?"
            ),
            input_filtered=False,
            output_filtered=False,
        )
        assert response.structured_content.answer == "Hello! How can I help?"
        assert not response.input_filtered


# ============================================================
# 17.2 Process isolation: Orchestrator memory
# ============================================================


class TestOrchestratorIsolation:
    """Verify Orchestrator process doesn't hold SQLCipher key material.

    In the multi-process architecture, the Orchestrator connects to
    DB Proxy Socket A via IPC — it never receives the master key.
    """

    def test_orchestrator_target_does_not_import_keysource(self):
        """The Orchestrator target function doesn't import keysource."""
        from backend.processes.targets import _orchestrator_target

        source = inspect.getsource(_orchestrator_target)
        assert "keysource" not in source
        assert "master_key" not in source

    def test_orchestrator_module_does_not_import_keysource(self):
        """The orchestrator module doesn't import keysource."""
        from backend.processes import orchestrator

        source = _get_package_source(orchestrator)
        assert "keysource" not in source
        assert "master_key" not in source

    def test_orchestrator_target_does_not_import_database(self):
        """The Orchestrator target doesn't import DatabaseManager."""
        from backend.processes.targets import _orchestrator_target

        source = inspect.getsource(_orchestrator_target)
        assert "DatabaseManager" not in source

    def test_orchestrator_connects_to_socket_a_only(self):
        """The Orchestrator args receive Socket A path, not Socket B."""
        from backend.processes.supervisor import (
            DB_PROXY_SOCKET_A,
            DB_PROXY_SOCKET_B,
            build_topology,
        )

        specs = build_topology()
        orchestrator_spec = next(
            process_spec
            for process_spec in specs
            if process_spec.name == "orchestrator"
        )
        fake_secrets = {
            "db-proxy": b"\x00" * 32,
            "db-proxy-b": b"\x11" * 32,
            "db-proxy-c": b"\x22" * 32,
            "orchestrator": b"\x33" * 32,
            "credential-proxy": b"\x44" * 32,
            "signing-proxy": b"\x55" * 32,
        }
        args = orchestrator_spec.args_factory(fake_secrets)
        # First arg is the DB Proxy socket path
        assert args[0] == DB_PROXY_SOCKET_A
        # Orchestrator should NOT have Socket B path
        assert DB_PROXY_SOCKET_B not in args


# ============================================================
# 17.3 Process isolation: FastAPI holds only signing key
# ============================================================


class TestFastAPIIsolation:
    """Verify FastAPI process holds no keys — delegates signing to Signing Proxy.

    FastAPI connects to Signing Proxy via IPC for all JWT operations.
    It never sees the Ed25519 private key, master key, or database.
    """

    def test_fastapi_target_does_not_import_keysource(self):
        """The FastAPI target function doesn't import keysource."""
        from backend.processes.targets import _fastapi_target

        source = inspect.getsource(_fastapi_target)
        assert "keysource" not in source
        assert "master_key" not in source.lower()

    def test_fastapi_target_does_not_import_database(self):
        """The FastAPI target doesn't import DatabaseManager."""
        from backend.processes.targets import _fastapi_target

        source = inspect.getsource(_fastapi_target)
        assert "DatabaseManager" not in source

    def test_fastapi_app_module_has_no_signing_key(self):
        """fastapi_app does not hold Ed25519PrivateKey (delegated to Signing Proxy)."""
        from backend.processes import fastapi_app

        source = _get_package_source(fastapi_app)
        assert "Ed25519PrivateKey" not in source
        assert "sqlcipher" not in source.lower()
        assert "master_key" not in source


# ============================================================
# 17.4 DB Proxy security: Socket A rejects credential operations
# ============================================================


class TestDBProxySocketASecurity:
    """Verify Orchestrator (Socket A) cannot access credential operations."""

    def test_allowlist_excludes_credentials_from_channel_a(self):
        """Channel A allowlist does not include credential operations."""
        from backend.processes.db_proxy import (
            CHANNEL_A,
            build_allowlist,
        )

        allowlist = build_allowlist()
        credential_ops = [
            "store_credential",
            "get_credential",
            "delete_credential",
        ]
        for op in credential_ops:
            assert not allowlist.is_allowed(CHANNEL_A, op), (
                f"Channel A should not allow '{op}'"
            )

    def test_allowlist_excludes_admin_from_channel_a(self):
        """Channel A allowlist does not include admin-only operations."""
        from backend.processes.db_proxy import (
            CHANNEL_A,
            build_allowlist,
        )

        allowlist = build_allowlist()
        admin_ops = ["create_integration", "delete_integration"]
        for op in admin_ops:
            assert not allowlist.is_allowed(CHANNEL_A, op), (
                f"Channel A should not allow '{op}'"
            )

    def test_allowlist_allows_messaging_on_channel_a(self):
        """Channel A allows standard messaging operations."""
        from backend.processes.db_proxy import (
            CHANNEL_A,
            build_allowlist,
        )

        allowlist = build_allowlist()
        messaging_ops = ["save_message", "load_recent_messages", "load_context"]
        for op in messaging_ops:
            assert allowlist.is_allowed(CHANNEL_A, op), f"Channel A should allow '{op}'"

    def test_channel_b_rejects_credentials(self):
        """Channel B (FastAPI admin) rejects credential operations."""
        from backend.processes.db_proxy import (
            CHANNEL_B,
            build_allowlist,
        )

        allowlist = build_allowlist()
        credential_ops = [
            "store_credential",
            "get_credential",
            "delete_credential",
            "create_integration",
            "list_integrations",
            "delete_integration",
        ]
        for op in credential_ops:
            assert not allowlist.is_allowed(CHANNEL_B, op), (
                f"Channel B should not allow '{op}'"
            )

    def test_channel_b_allows_admin(self):
        """Channel B allows admin-only operations."""
        from backend.processes.db_proxy import (
            CHANNEL_B,
            build_allowlist,
        )

        allowlist = build_allowlist()
        admin_ops = [
            "get_signing_material",
            "get_stats",
            "initialize",
        ]
        for op in admin_ops:
            assert allowlist.is_allowed(CHANNEL_B, op), f"Channel B should allow '{op}'"

    def test_channel_c_allows_credentials(self):
        """Channel C (Credential Proxy) allows credential operations."""
        from backend.processes.db_proxy import (
            CHANNEL_C,
            build_allowlist,
        )

        allowlist = build_allowlist()
        credential_ops = [
            "store_credential",
            "get_credential",
            "delete_credential",
            "create_integration",
            "list_integrations",
            "delete_integration",
        ]
        for op in credential_ops:
            assert allowlist.is_allowed(CHANNEL_C, op), f"Channel C should allow '{op}'"

    def test_channel_c_rejects_non_credential(self):
        """Channel C rejects non-credential operations."""
        from backend.processes.db_proxy import (
            CHANNEL_C,
            build_allowlist,
        )

        allowlist = build_allowlist()
        non_cred_ops = [
            "save_message",
            "load_context",
            "get_signing_material",
            "get_stats",
        ]
        for op in non_cred_ops:
            assert not allowlist.is_allowed(CHANNEL_C, op), (
                f"Channel C should not allow '{op}'"
            )


# ============================================================
# 17.5 Crash recovery test
# ============================================================


class TestCrashRecovery:
    """Verify supervisor restart policy and process lifecycle."""

    def test_process_spec_has_correct_targets(self):
        """Each process spec has a callable target function."""
        from backend.processes.supervisor import build_topology

        specs = build_topology()
        for process_spec in specs:
            assert callable(process_spec.target), (
                f"Process {process_spec.name} has non-callable target"
            )

    def test_process_spec_max_restarts_configurable(self):
        """ProcessSpec accepts max_restarts parameter."""
        from backend.ipc.supervisor import ProcessSpec

        spec = ProcessSpec(
            name="test",
            target=lambda: None,
            args_factory=lambda s: (),
            max_restarts=5,
        )
        assert spec.max_restarts == 5

    def test_process_spec_default_max_restarts(self):
        """ProcessSpec has a sensible default max_restarts."""
        from backend.ipc.supervisor import ProcessSpec

        spec = ProcessSpec(
            name="test",
            target=lambda: None,
            args_factory=lambda s: (),
        )
        assert spec.max_restarts >= 1


# ============================================================
# 17.6 IPC streaming test
# ============================================================


class TestIPCStreaming:
    """Verify StreamEvent token delivery through IPC chain."""

    def test_stream_event_serialization(self):
        """StreamEvent serializes with type and data fields."""
        event = StreamEvent(
            request_id="req-1",
            event_type="token",
            data={"text": "Hello"},
        )
        data = event.model_dump()
        assert data["event_type"] == "token"
        assert data["data"]["text"] == "Hello"

    def test_stream_event_types(self):
        """Multiple stream event types are supported."""
        for event_type in ("token", "tool_status", "confirmation", "done"):
            event = StreamEvent(
                request_id="req-1",
                event_type=event_type,
                data={},
            )
            assert event.event_type == event_type

    @pytest.mark.asyncio
    async def test_stream_event_queue_routing(self):
        """StreamEvents route to correct session queue by request_id."""
        from backend.processes.fastapi_app import OrchestratorClient

        # Create a mock IPC that we control
        class FakeIPC:
            def __init__(self):
                self._messages = asyncio.Queue()

            async def receive(self):
                return await self._messages.get()

            async def send(self, message):
                pass

            async def close(self):
                pass

        ipc = FakeIPC()
        client = OrchestratorClient(ipc)

        # Register a session queue
        queue = asyncio.Queue()
        client.register_session_queue("session-1", queue)
        client._request_to_session["req-1"] = "session-1"

        # Start reader
        await client.start()

        # Inject a stream event
        event = StreamEvent(
            request_id="req-1",
            event_type="token",
            data={"text": "hi"},
        )
        await ipc._messages.put(event)

        # Verify it arrives in the session queue
        received = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert received.event_type == "token"
        assert received.data["text"] == "hi"

        await client.stop()


# ============================================================
# 17.7 Update existing security isolation tests
# ============================================================


class TestProcessArchitectureSecurityIsolation:
    """Security isolation tests updated for multi-process architecture.

    Verifies that the process boundaries enforce the security model:
    - Master key only in DB Proxy process
    - Signing key only in FastAPI process
    - IPC allowlists enforce least-privilege
    """

    def test_db_proxy_is_only_key_holder(self):
        """Only _db_proxy_target imports keysource and DatabaseManager."""
        from backend.processes.targets import (
            _credential_proxy_target,
            _db_proxy_target,
            _fastapi_target,
            _orchestrator_target,
            _signing_proxy_target,
        )

        db_src = inspect.getsource(_db_proxy_target)
        orch_src = inspect.getsource(_orchestrator_target)
        fa_src = inspect.getsource(_fastapi_target)
        cred_src = inspect.getsource(_credential_proxy_target)
        sign_src = inspect.getsource(_signing_proxy_target)

        assert "get_key_source" in db_src
        assert "DatabaseManager" in db_src
        assert "get_key_source" not in orch_src
        assert "get_key_source" not in fa_src
        assert "get_key_source" not in cred_src
        assert "get_key_source" not in sign_src

    def test_signing_key_only_in_signing_proxy(self):
        """Ed25519 signing key is only held by Signing Proxy, not FastAPI."""
        from backend.processes import fastapi_app, signing_proxy
        from backend.processes.targets import (
            _credential_proxy_target,
            _orchestrator_target,
        )

        sign_src = _get_package_source(signing_proxy)
        fa_src = _get_package_source(fastapi_app)
        orch_src = inspect.getsource(_orchestrator_target)
        cred_src = inspect.getsource(_credential_proxy_target)

        # Signing Proxy holds the key
        assert "Ed25519PrivateKey" in sign_src
        # FastAPI does NOT hold the key
        assert "Ed25519PrivateKey" not in fa_src
        assert "Ed25519PrivateKey" not in orch_src
        assert "Ed25519PrivateKey" not in cred_src

    def test_topology_has_five_processes(self):
        """Build topology returns 5 process specs."""
        from backend.processes.supervisor import build_topology

        specs = build_topology()
        assert len(specs) == 5
        names = {process_spec.name for process_spec in specs}
        assert names == {
            "db-proxy",
            "signing-proxy",
            "credential-proxy",
            "orchestrator",
            "fastapi",
        }

    def test_signing_proxy_no_db_access(self):
        """Signing Proxy does not import keysource or DatabaseManager."""
        from backend.processes import signing_proxy

        source = _get_package_source(signing_proxy)
        assert "get_key_source" not in source
        assert "DatabaseManager" not in source
        assert "store_credential" not in source

    def test_signing_proxy_allowlist_exact(self):
        """Signing Proxy allows exactly its defined operations."""
        from backend.processes.signing_proxy import SIGNING_OPERATIONS

        expected_operations = frozenset(
            {
                "sign_token",
                "verify_token",
                "setup_password",
                "verify_password",
                "check_password",
                "get_auth_status",
            }
        )
        assert expected_operations == SIGNING_OPERATIONS

    def test_fastapi_uses_credential_client_for_integrations(self):
        """FastAPI integration routes use CredentialClient, not AdminDBClient."""
        from backend.processes import fastapi_app

        source = _get_package_source(fastapi_app)
        # Integration routes should use _get_credential_client
        assert "_get_credential_client" in source
        # CredentialClient class should exist
        assert "class CredentialClient" in source

    def test_no_state_check_middleware_in_new_app(self):
        """Multi-process FastAPI app does not add StateCheckMiddleware."""
        from backend.processes import fastapi_app

        source = inspect.getsource(fastapi_app.create_app)
        assert "add_middleware(StateCheckMiddleware" not in source

    def test_no_setup_unlock_in_new_app(self):
        """Multi-process FastAPI app has no /setup or /unlock routes."""
        from backend.processes import fastapi_app

        source = _get_package_source(fastapi_app)
        assert "'/setup'" not in source
        assert "'/unlock'" not in source


# ============================================================
# Credential Proxy isolation tests
# ============================================================


class TestCredentialProxyIsolation:
    """Verify Credential Proxy process isolation boundaries."""

    def test_credential_proxy_does_not_import_keysource(self):
        """The credential_proxy module doesn't import keysource."""
        from backend.processes import credential_proxy

        source = _get_package_source(credential_proxy)
        assert "keysource" not in source
        assert "master_key" not in source

    def test_credential_proxy_does_not_import_database(self):
        """The credential_proxy module doesn't import DatabaseManager."""
        from backend.processes import credential_proxy

        source = _get_package_source(credential_proxy)
        assert "DatabaseManager" not in source

    def test_credential_proxy_does_not_import_signing_key(self):
        """The credential_proxy module doesn't import Ed25519."""
        from backend.processes import credential_proxy

        source = _get_package_source(credential_proxy)
        assert "Ed25519" not in source
        assert "signing_key" not in source

    def test_credential_proxy_allowlist_exact(self):
        """Credential Proxy allowlist contains credential and LLM-config operations."""
        from backend.processes.credential_proxy import CREDENTIAL_OPERATIONS

        expected = frozenset(
            {
                "store_credential",
                "get_credential",
                "delete_credential",
                "create_integration",
                "list_integrations",
                "delete_integration",
                "store_llm_config",
                "get_llm_config_meta",
                "list_llm_configs",
                "create_llm_config",
                "update_llm_config",
                "delete_llm_config",
                "activate_llm_config",
                "get_llm_config_full",
            }
        )
        assert expected == CREDENTIAL_OPERATIONS

    def test_credential_proxy_target_in_topology(self):
        """Credential Proxy appears in the supervisor topology."""
        from backend.processes.supervisor import build_topology

        specs = build_topology()
        names = [process_spec.name for process_spec in specs]
        assert "credential-proxy" in names

    def test_credential_proxy_depends_on_db_proxy(self):
        """Credential Proxy depends only on db-proxy."""
        from backend.processes.supervisor import build_topology

        specs = build_topology()
        credential_spec = next(
            process_spec
            for process_spec in specs
            if process_spec.name == "credential-proxy"
        )
        assert "db-proxy" in credential_spec.depends_on

    def test_fastapi_depends_on_credential_proxy(self):
        """FastAPI depends on both orchestrator and credential-proxy."""
        from backend.processes.supervisor import build_topology

        specs = build_topology()
        fastapi_spec = next(
            process_spec for process_spec in specs if process_spec.name == "fastapi"
        )
        assert "orchestrator" in fastapi_spec.depends_on
        assert "credential-proxy" in fastapi_spec.depends_on


# ============================================================
# 17.8 Docker network isolation (static analysis)
# ============================================================

# NOTE: Docker runtime tests are in test_network_isolation.py.
# This test verifies the compose file prevents direct access
# between non-adjacent containers.


class TestDockerNetworkIsolationArchitecture:
    """Architectural tests for Docker network segmentation."""

    def test_compose_uses_point_to_point_networks(self):
        """Current compose file defines the active network layout."""
        yaml = pytest.importorskip("yaml")

        compose_path = (
            Path(__file__).resolve().parents[2] / "docker" / "docker-compose.dev.yml"
        )
        if not compose_path.exists():
            pytest.skip(
                "docker-compose.dev.yml not available (running inside container)"
            )
        compose = yaml.safe_load(compose_path.read_text())
        networks = set(compose["networks"].keys())
        assert "bm-caddy" in networks
        assert "bm-searxng" in networks
        # Old network names should not exist
        assert "internal" not in networks
        assert "search" not in networks

    def test_no_service_on_all_networks(self):
        """No single service connects to all active networks."""
        yaml = pytest.importorskip("yaml")

        compose_path = (
            Path(__file__).resolve().parents[2] / "docker" / "docker-compose.dev.yml"
        )
        if not compose_path.exists():
            pytest.skip(
                "docker-compose.dev.yml not available (running inside container)"
            )
        compose = yaml.safe_load(compose_path.read_text())
        all_networks = set(compose["networks"].keys())
        for service_name, service in compose["services"].items():
            service_networks = set(service.get("networks", []))
            assert service_networks != all_networks, (
                f"Service {service_name} should not connect to all networks"
            )
