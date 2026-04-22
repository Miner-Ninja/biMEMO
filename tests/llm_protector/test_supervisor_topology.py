"""Tests for the biMEMO process supervisor topology.

Covers process topology definition, dependency order, socket
path configuration, and health check assignments.
Does not spawn actual processes.
"""

import asyncio

import pytest
from backend.processes.supervisor import build_topology
from backend.processes.targets import (
    CREDENTIAL_PROXY_SOCKET,
    DB_PROXY_SOCKET_A,
    DB_PROXY_SOCKET_B,
    DB_PROXY_SOCKET_C,
    ORCHESTRATOR_SOCKET,
    SIGNING_PROXY_SOCKET,
    _make_socket_probe,
)


# Complete fake secrets for all 6 secret keys used by the topology.
FAKE_SECRETS = {
    "db-proxy": b"\x00" * 32,
    "db-proxy-b": b"\x11" * 32,
    "db-proxy-c": b"\x22" * 32,
    "orchestrator": b"\x33" * 32,
    "credential-proxy": b"\x44" * 32,
    "signing-proxy": b"\x55" * 32,
}


class TestProcessTopology:
    """Tests for biMEMO process topology definition."""

    def test_topology_has_five_processes(self) -> None:
        """Build topology returns five process specs."""
        specs = build_topology()
        assert len(specs) == 5

    def test_process_names(self) -> None:
        """Process names include all five processes."""
        specs = build_topology()
        names = [process_spec.name for process_spec in specs]
        assert "db-proxy" in names
        assert "signing-proxy" in names
        assert "credential-proxy" in names
        assert "orchestrator" in names
        assert "fastapi" in names

    def test_dependency_order(self) -> None:
        """Middle tier depends on db-proxy, FastAPI on all three."""
        specs = build_topology()
        by_name = {process_spec.name: process_spec for process_spec in specs}

        assert by_name["db-proxy"].depends_on == []
        assert by_name["signing-proxy"].depends_on == ["db-proxy"]
        assert by_name["credential-proxy"].depends_on == ["db-proxy"]
        assert by_name["orchestrator"].depends_on == ["db-proxy"]
        assert "orchestrator" in by_name["fastapi"].depends_on
        assert "credential-proxy" in by_name["fastapi"].depends_on
        assert "signing-proxy" in by_name["fastapi"].depends_on

    def test_socket_paths_distinct(self) -> None:
        """All socket paths are unique."""
        paths = {
            DB_PROXY_SOCKET_A,
            DB_PROXY_SOCKET_B,
            DB_PROXY_SOCKET_C,
            ORCHESTRATOR_SOCKET,
            CREDENTIAL_PROXY_SOCKET,
            SIGNING_PROXY_SOCKET,
        }
        assert len(paths) == 6

    def test_args_factory_produces_tuples(self) -> None:
        """Each args_factory returns a tuple."""
        specs = build_topology()
        for spec in specs:
            args = spec.args_factory(FAKE_SECRETS)
            assert isinstance(args, tuple)
            assert len(args) >= 4

    def test_db_proxy_receives_three_socket_paths(self) -> None:
        """DB Proxy args include Socket A, B, and C paths."""
        specs = build_topology()
        db_spec = next(
            process_spec for process_spec in specs if process_spec.name == "db-proxy"
        )
        args = db_spec.args_factory(FAKE_SECRETS)
        assert args[0] == DB_PROXY_SOCKET_A
        assert args[2] == DB_PROXY_SOCKET_B
        assert args[4] == DB_PROXY_SOCKET_C

    def test_fastapi_receives_four_connections(self) -> None:
        """FastAPI args include orchestrator, DB proxy B, credential, and signing."""
        specs = build_topology()
        fastapi_spec = next(
            process_spec for process_spec in specs if process_spec.name == "fastapi"
        )
        args = fastapi_spec.args_factory(FAKE_SECRETS)
        assert args[0] == ORCHESTRATOR_SOCKET
        assert args[2] == DB_PROXY_SOCKET_B
        assert args[4] == CREDENTIAL_PROXY_SOCKET
        assert args[6] == SIGNING_PROXY_SOCKET

    def test_credential_proxy_receives_both_sockets(self) -> None:
        """Credential Proxy args include its own socket and DB Proxy Socket C."""
        specs = build_topology()
        credential_spec = next(
            process_spec
            for process_spec in specs
            if process_spec.name == "credential-proxy"
        )
        args = credential_spec.args_factory(FAKE_SECRETS)
        assert args[0] == CREDENTIAL_PROXY_SOCKET
        assert args[2] == DB_PROXY_SOCKET_C

    def test_signing_proxy_receives_both_sockets(self) -> None:
        """Signing Proxy args include its own socket and DB Proxy Socket B."""
        specs = build_topology()
        signing_spec = next(
            process_spec
            for process_spec in specs
            if process_spec.name == "signing-proxy"
        )
        args = signing_spec.args_factory(FAKE_SECRETS)
        assert args[0] == SIGNING_PROXY_SOCKET
        assert args[2] == DB_PROXY_SOCKET_B

    def test_manual_password_goes_to_key_holding_processes_only(self) -> None:
        """Manual startup password is passed only where unlock and UI seed need it."""
        specs = build_topology("manual-startup-password")
        by_name = {process_spec.name: process_spec for process_spec in specs}

        db_args = by_name["db-proxy"].args_factory(FAKE_SECRETS)
        signing_args = by_name["signing-proxy"].args_factory(FAKE_SECRETS)
        credential_args = by_name["credential-proxy"].args_factory(FAKE_SECRETS)
        orchestrator_args = by_name["orchestrator"].args_factory(FAKE_SECRETS)
        fastapi_args = by_name["fastapi"].args_factory(FAKE_SECRETS)

        assert db_args[-1] == "manual-startup-password"
        assert signing_args[-1] == "manual-startup-password"
        assert "manual-startup-password" not in credential_args
        assert "manual-startup-password" not in orchestrator_args
        assert "manual-startup-password" not in fastapi_args


class TestSocketProbe:
    """Tests for _make_socket_probe health check factory."""

    @pytest.mark.asyncio
    async def test_probe_returns_true_when_socket_listening(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Probe succeeds when a Unix socket is accepting connections."""
        sock_path = str(tmp_path / "test.sock")

        server = await asyncio.start_unix_server(
            lambda reader, writer: writer.close(),
            sock_path,
        )
        try:
            probe = _make_socket_probe(sock_path)
            assert await probe() is True
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_probe_returns_false_when_no_socket(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Probe returns False when socket file does not exist."""
        sock_path = str(tmp_path / "nonexistent.sock")
        probe = _make_socket_probe(sock_path)
        assert await probe() is False

    @pytest.mark.asyncio
    async def test_probe_returns_false_on_connection_refused(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Probe returns False when socket exists but nobody is listening."""
        sock_path = str(tmp_path / "dead.sock")
        # Create a socket file then immediately close the server
        server = await asyncio.start_unix_server(
            lambda reader, writer: writer.close(), sock_path
        )
        server.close()
        await server.wait_closed()
        # Socket file may still exist but no one is listening
        probe = _make_socket_probe(sock_path)
        assert await probe() is False


class TestHealthCheckAssignment:
    """Tests that health checks are correctly assigned in the topology."""

    def test_socket_exposing_processes_have_health_checks(self) -> None:
        """Socket-exposing processes in the topology all have health checks."""
        specs = build_topology()
        by_name = {process_spec.name: process_spec for process_spec in specs}
        for name in ("db-proxy", "signing-proxy", "credential-proxy", "orchestrator"):
            assert by_name[name].health_check is not None, (
                f"{name} should have a health_check"
            )

    def test_fastapi_has_no_health_check(self) -> None:
        """FastAPI has no health check since nothing depends on it."""
        specs = build_topology()
        by_name = {process_spec.name: process_spec for process_spec in specs}
        assert by_name["fastapi"].health_check is None
