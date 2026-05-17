"""Tests for tool parameter validation.

Covers path traversal, sensitive paths, URL scheme
allowlist, numeric IP detection, and SSRF prevention.
"""

from unittest.mock import patch

import pytest
from backend.tools.validation import (
    ValidationError,
    _dns_lookup,
    check_numeric_ip,
    check_path_traversal,
    check_sensitive_path,
    check_url_scheme,
    resolve_and_check,
    validate_url,
)


class TestPathTraversal:
    """Tests for path traversal detection."""

    def test_rejects_dotdot(self):
        """Verify ../ is rejected."""
        with pytest.raises(ValidationError, match="traversal"):
            check_path_traversal("../etc/passwd")

    def test_rejects_middle_dotdot(self):
        """Verify path/../other is rejected."""
        with pytest.raises(ValidationError, match="traversal"):
            check_path_traversal("/home/user/../root")

    def test_allows_normal_path(self):
        """Verify normal paths pass."""
        check_path_traversal("/home/user/file.txt")

    def test_allows_dots_in_filename(self):
        """Verify dots in filenames pass."""
        check_path_traversal("/home/user/file.tar.gz")


class TestSensitivePath:
    """Tests for sensitive path detection."""

    def test_rejects_ssh(self):
        """Verify .ssh is rejected."""
        with pytest.raises(ValidationError, match="sensitive"):
            check_sensitive_path("/home/user/.ssh/id_rsa")

    def test_rejects_env(self):
        """Verify .env is rejected."""
        with pytest.raises(ValidationError, match="sensitive"):
            check_sensitive_path("/app/.env")

    def test_rejects_bashrc(self):
        """Verify .bashrc is rejected."""
        with pytest.raises(ValidationError, match="sensitive"):
            check_sensitive_path("/home/user/.bashrc")

    def test_allows_normal_path(self):
        """Verify normal paths pass."""
        check_sensitive_path("/home/user/docs/file.txt")


class TestUrlScheme:
    """Tests for URL scheme validation."""

    def test_allows_https(self):
        """Verify HTTPS is allowed."""
        check_url_scheme("https://example.com")

    def test_rejects_http(self):
        """Verify HTTP is restricted to the assistant policy allowlist."""
        with pytest.raises(ValidationError, match="allowlist"):
            check_url_scheme("http://example.com")

    def test_rejects_file(self):
        """Verify file:// is rejected."""
        with pytest.raises(ValidationError, match="scheme"):
            check_url_scheme("file:///etc/passwd")

    def test_rejects_ftp(self):
        """Verify ftp:// is rejected."""
        with pytest.raises(ValidationError, match="scheme"):
            check_url_scheme("ftp://example.com")


class TestNumericIp:
    """Tests for numeric IP format detection."""

    def test_rejects_hex(self):
        """Verify hex IP is rejected."""
        with pytest.raises(ValidationError, match="Numeric"):
            check_numeric_ip("0x7f000001")

    def test_rejects_octal(self):
        """Verify octal IP is rejected."""
        with pytest.raises(ValidationError, match="Numeric"):
            check_numeric_ip("0177000001")

    def test_rejects_decimal(self):
        """Verify long decimal IP is rejected."""
        with pytest.raises(ValidationError, match="Numeric"):
            check_numeric_ip("2130706433")

    def test_allows_hostname(self):
        """Verify normal hostname passes."""
        check_numeric_ip("example.com")


class TestResolveAndCheck:
    """Tests for DNS resolution and IP checking."""

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_allows_localhost(self, mock_dns):
        """Verify localhost is allowed by the single assistant policy."""
        mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
        ip = resolve_and_check("local.example.com")
        assert ip == "127.0.0.1"

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_allows_private(self, mock_dns):
        """Verify RFC1918 addresses are allowed by the single assistant policy."""
        mock_dns.return_value = [(2, 1, 6, "", ("192.168.1.1", 0))]
        ip = resolve_and_check("private.example.com")
        assert ip == "192.168.1.1"

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_rejects_link_local(self, mock_dns):
        """Verify link-local addresses stay blocked."""
        mock_dns.return_value = [(2, 1, 6, "", ("169.254.1.1", 0))]
        with pytest.raises(ValidationError, match="blocked"):
            resolve_and_check("evil.example.com")

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_allows_public(self, mock_dns):
        """Verify public IP is allowed."""
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        ip = resolve_and_check("example.com")
        assert ip == "93.184.216.34"

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_dns_failure(self, mock_dns):
        """Verify DNS failure raises error."""
        import socket

        mock_dns.side_effect = socket.gaierror("fail")
        with pytest.raises(ValidationError, match="DNS"):
            resolve_and_check("nonexistent.invalid")

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_empty_results(self, mock_dns):
        """Verify empty DNS results raise error."""
        mock_dns.return_value = []
        with pytest.raises(ValidationError, match="No DNS"):
            resolve_and_check("example.com")

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_dns_lookup_restores_default_timeout_on_success(self, mock_dns):
        """Verify DNS lookup does not leak global socket timeout state."""
        import socket

        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(123.0)
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        try:
            assert _dns_lookup("example.com") == mock_dns.return_value
            assert socket.getdefaulttimeout() == 123.0
        finally:
            socket.setdefaulttimeout(previous_timeout)

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_dns_lookup_restores_default_timeout_on_failure(self, mock_dns):
        """Verify DNS lookup restores timeout even when resolution fails."""
        import socket

        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(123.0)
        mock_dns.side_effect = socket.gaierror("fail")
        try:
            with pytest.raises(ValidationError, match="DNS"):
                _dns_lookup("missing.invalid")
            assert socket.getdefaulttimeout() == 123.0
        finally:
            socket.setdefaulttimeout(previous_timeout)


class TestValidateUrl:
    """Tests for full URL validation."""

    @patch("backend.tools.validation.socket.getaddrinfo")
    def test_valid_https(self, mock_dns):
        """Verify valid HTTPS URL passes."""
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        ip = validate_url("https://example.com/path")
        assert ip == "93.184.216.34"

    def test_rejects_http(self):
        """Verify non-allowlisted HTTP is rejected before DNS."""
        with pytest.raises(ValidationError, match="allowlist"):
            validate_url("http://example.com")

    def test_rejects_no_hostname(self):
        """Verify URL without hostname rejected."""
        with pytest.raises(ValidationError, match="hostname"):
            validate_url("https://")
