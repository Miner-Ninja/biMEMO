"""Tests for Docker network isolation topology.

Verifies that the active docker-compose.yml keeps service networks
separated where direct communication should not happen.

These tests parse the compose file statically — no Docker daemon required.
"""

from pathlib import Path

import pytest


yaml = pytest.importorskip("yaml")

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"

pytestmark = pytest.mark.skipif(
    not COMPOSE_PATH.exists(),
    reason="docker-compose.yml not available (running inside container)",
)


@pytest.fixture(scope="module")
def compose() -> dict[str, object]:
    """Load the active docker-compose.yml as a dict."""
    return yaml.safe_load(COMPOSE_PATH.read_text())


class TestNetworkTopology:
    """Verify point-to-point network assignments."""

    def test_two_networks_defined(self, compose: dict[str, object]) -> None:
        """Exactly two networks are defined in the current active compose."""
        nets = compose["networks"]
        assert set(nets.keys()) == {"bm-caddy", "bm-searxng"}

    def test_bimemo_networks(self, compose: dict[str, object]) -> None:
        """BiMEMO connects to bm-caddy and bm-searxng only."""
        nets = compose["services"]["bimemo"]["networks"]
        assert set(nets) == {"bm-caddy", "bm-searxng"}

    def test_caddy_networks(self, compose: dict[str, object]) -> None:
        """Caddy connects to bm-caddy only."""
        nets = compose["services"]["caddy"]["networks"]
        assert set(nets) == {"bm-caddy"}

    def test_frontend_networks(self, compose: dict[str, object]) -> None:
        """Frontend connects to bm-caddy only."""
        nets = compose["services"]["frontend"]["networks"]
        assert set(nets) == {"bm-caddy"}

    def test_whisper_networks(self, compose: dict[str, object]) -> None:
        """Whisper connects to bm-caddy only."""
        nets = compose["services"]["whisper"]["networks"]
        assert set(nets) == {"bm-caddy"}

    def test_searxng_networks(self, compose: dict[str, object]) -> None:
        """SearXNG connects to bm-searxng only."""
        nets = compose["services"]["searxng"]["networks"]
        assert set(nets) == {"bm-searxng"}

    def test_frontend_cannot_reach_searxng(self, compose: dict[str, object]) -> None:
        """Frontend and SearXNG share no network — no direct communication."""
        frontend_nets = set(compose["services"]["frontend"]["networks"])
        searxng_nets = set(compose["services"]["searxng"]["networks"])
        assert frontend_nets & searxng_nets == set()

    def test_searxng_cannot_reach_caddy(self, compose: dict[str, object]) -> None:
        """SearXNG and Caddy share no network — no direct communication."""
        searxng_nets = set(compose["services"]["searxng"]["networks"])
        caddy_nets = set(compose["services"]["caddy"]["networks"])
        assert searxng_nets & caddy_nets == set()

    def test_caddy_cannot_reach_searxng(self, compose: dict[str, object]) -> None:
        """Caddy and SearXNG share no network — no direct communication."""
        caddy_nets = set(compose["services"]["caddy"]["networks"])
        searxng_nets = set(compose["services"]["searxng"]["networks"])
        assert caddy_nets & searxng_nets == set()
