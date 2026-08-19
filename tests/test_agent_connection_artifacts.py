"""Tests for agent connection artifacts.

Verifies the TLS certificate generation script and the Caddy dev config
contain the required entries for secure agent connections.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────────
# R8: TLS certificate SAN entries
# ──────────────────────────────────────────────────────────────────────


class TestTlsCertSan:
    """R8 / S8.1 — cert SAN includes localhost, 127.0.0.1, LAN/host."""

    def test_generate_dev_tls_creates_ca_and_cert(self) -> None:
        """scripts/generate_dev_tls.sh creates a CA and a CA-signed cert."""
        script = (
            Path(__file__).resolve().parent.parent / "scripts" / "generate_dev_tls.sh"
        )

        if not script.exists():
            pytest.skip("generate_dev_tls.sh not found")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            Path.cwd()

            try:
                # We need to run from the project root so relative paths work
                env = {**dict(__import__("os").environ)}
                # Point CERT_DIR into tmp
                env["CERT_DIR"] = str(tmp_path / "docker" / "caddy" / "certs")

                # The script uses relative paths; change into tmp but we need
                # the script to write into our temp dir. Since the script hardcodes
                # CERT_DIR="docker/caddy/certs", we'll run it from a tmp dir.
                work_dir = tmp_path
                work_dir.mkdir(exist_ok=True)

                result = subprocess.run(
                    ["sh", str(script)],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )

                if result.returncode != 0:
                    print("STDOUT:", result.stdout)
                    print("STDERR:", result.stderr)

                assert result.returncode == 0, f"Script failed: {result.stderr}"

                certs_dir = work_dir / "docker" / "caddy" / "certs"
                ca_dir = work_dir / "docker" / "caddy" / "ca"

                # CA files must exist in the sibling dir (not under certs/).
                assert (ca_dir / "ca-key.pem").exists(), "CA key not created"
                assert (ca_dir / "ca-cert.pem").exists(), "CA cert not created"

                # Security invariant: the CA private key must NOT live under
                # certs/, which docker-compose mounts into the Caddy container.
                assert not (certs_dir / "ca" / "ca-key.pem").exists(), (
                    "CA key must not be under the container-mounted certs/ dir"
                )
                assert not (certs_dir / "ca-key.pem").exists(), (
                    "CA key must not be under the container-mounted certs/ dir"
                )

                # Server cert and key must exist
                assert (certs_dir / "cert.pem").exists(), "Server cert not created"
                assert (certs_dir / "key.pem").exists(), "Server key not created"

                # Parse the server cert to check SAN
                cert_path = certs_dir / "cert.pem"
                san_result = subprocess.run(
                    ["openssl", "x509", "-noout", "-text", "-in", str(cert_path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert san_result.returncode == 0, "Failed to parse cert"
                cert_text = san_result.stdout

                # SAN must include localhost and 127.0.0.1
                assert "DNS:localhost" in cert_text, "SAN missing DNS:localhost"
                assert "IP Address:127.0.0.1" in cert_text, "SAN missing IP:127.0.0.1"

                # Verify it was signed by the CA (not self-signed)
                verify_result = subprocess.run(
                    [
                        "openssl",
                        "verify",
                        "-CAfile",
                        str(ca_dir / "ca-cert.pem"),
                        str(cert_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert "OK" in verify_result.stdout, (
                    f"Cert not signed by CA: {verify_result.stdout}"
                )

            finally:
                Path.cwd().chmod(0o755)  # ensure we can cleanup


# ──────────────────────────────────────────────────────────────────────
# R11: Caddy dev config
# ──────────────────────────────────────────────────────────────────────


class TestCaddyDevConfig:
    """R11 / S11.1, S11.2 — Caddyfile.dev has /mcp in @api matcher."""

    @pytest.fixture
    def caddyfile(self) -> Path:
        """Path to Caddyfile.dev."""
        return (
            Path(__file__).resolve().parent.parent
            / "docker"
            / "caddy"
            / "Caddyfile.dev"
        )

    def test_api_matcher_includes_mcp(self, caddyfile: Path) -> None:
        """The @api matcher includes path /mcp /mcp/*."""
        content = caddyfile.read_text()
        assert "path /mcp" in content, "@api matcher missing /mcp path"

    def test_no_plain_http_mcp_route(self, caddyfile: Path) -> None:
        """No plain-HTTP (:80) route serves /mcp."""
        content = caddyfile.read_text()

        # The :80 block should only have a redirect
        # Extract the :80 block
        in_port80 = False
        brace_depth = 0
        port80_block = ""

        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(":80"):
                in_port80 = True
                brace_depth = 0
                port80_block = ""
            if in_port80:
                port80_block += line + "\n"
                brace_depth += stripped.count("{")
                brace_depth -= stripped.count("}")
                if brace_depth <= 0 and "{" in port80_block:
                    break

        # :80 block should only contain redirect, no /mcp routing
        assert "/mcp" not in port80_block, "Plain-HTTP :80 block must not serve /mcp"
