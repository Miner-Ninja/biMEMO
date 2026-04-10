"""Parameter validation for tool calls.

Provides path traversal detection, URL scheme allowlist,
and SSRF prevention via resolve-first validation.
"""

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse


logger = logging.getLogger(__name__)

# Allowed URL schemes
_ALLOWED_SCHEMES = frozenset({"https"})

# Private/reserved IP ranges (RFC 1918/4193/6890)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("::ffff:0:0/96"),
]

# Regex for numeric IP formats (decimal, hex, octal)
_NUMERIC_IP_RE = re.compile(r"^(0x[0-9a-fA-F]+|0[0-7]+|\d{8,})$")

# Path traversal patterns
_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")

# Sensitive dot-files and directories
_SENSITIVE_PATHS = frozenset(
    {
        ".bashrc",
        ".profile",
        ".bash_profile",
        ".env",
        ".ssh",
        ".gnupg",
        ".config",
    }
)

# DNS resolution timeout
_DNS_TIMEOUT = 5.0


class ValidationError(Exception):
    """Raised when parameter validation fails."""


def _validation_error(message: str) -> ValidationError:
    """Build one validation error with a plain message."""
    return ValidationError(message)


def check_path_traversal(path: str) -> None:
    """Check for path traversal sequences.

    Args:
        path: File path to validate.

    Raises:
        ValidationError: If traversal detected.
    """
    if _TRAVERSAL_RE.search(path):
        raise _validation_error(f"Path traversal detected: {path}")


def check_sensitive_path(path: str) -> None:
    """Check for sensitive dot-files and directories.

    Args:
        path: File path to validate.

    Raises:
        ValidationError: If sensitive path detected.
    """
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if part in _SENSITIVE_PATHS:
            raise _validation_error(f"Write to sensitive path blocked: {part}")


def check_url_scheme(url: str) -> None:
    """Validate URL uses allowed scheme.

    Args:
        url: URL to validate.

    Raises:
        ValidationError: If scheme not allowed.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise _validation_error(f"URL scheme not allowed: {parsed.scheme}")


def check_numeric_ip(hostname: str) -> None:
    """Reject numeric IP formats that bypass DNS.

    Args:
        hostname: Hostname to check.

    Raises:
        ValidationError: If numeric IP detected.
    """
    if _NUMERIC_IP_RE.match(hostname):
        raise _validation_error(f"Numeric IP format not allowed: {hostname}")


def _dns_lookup(hostname: str) -> list[tuple]:
    """Resolve one hostname with the configured DNS timeout."""
    socket.setdefaulttimeout(_DNS_TIMEOUT)
    try:
        return socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
    except socket.gaierror as error:
        raise _validation_error(f"DNS resolution failed: {hostname}") from error


def _first_dns_ip(hostname: str, results: list[tuple]) -> str:
    """Return the first resolved IP address from a DNS lookup."""
    if not results:
        raise _validation_error(f"No DNS results for: {hostname}")
    return results[0][4][0]


def _blocked_network(
    ip_str: str,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """Return the blocked network containing one IP, if any."""
    addr = ipaddress.ip_address(ip_str)
    for network in _BLOCKED_NETWORKS:
        if addr in network:
            return network
    return None


def _resolved_ip_or_error(hostname: str) -> str:
    """Resolve one hostname and return the first IP or a validation error."""
    results = _dns_lookup(hostname)
    return _first_dns_ip(hostname, results)


def resolve_and_check(hostname: str) -> str:
    """Resolve hostname and check against blocked ranges.

    Args:
        hostname: Hostname to resolve.

    Returns:
        Resolved IP address string for pinning.

    Raises:
        ValidationError: If resolved IP is in blocked range.
    """
    check_numeric_ip(hostname)
    ip_str = _resolved_ip_or_error(hostname)
    network = _blocked_network(ip_str)
    if network is not None:
        raise _validation_error(f"Resolved IP {ip_str} is in blocked range {network}")
    return ip_str


def validate_url(url: str) -> str:
    """Full URL validation: scheme + SSRF prevention.

    Args:
        url: URL to validate.

    Returns:
        Resolved IP address for connection pinning.

    Raises:
        ValidationError: If any check fails.
    """
    check_url_scheme(url)
    hostname = _url_hostname(url)
    if not hostname:
        raise _validation_error("URL missing hostname")
    return resolve_and_check(hostname)


def _url_hostname(url: str) -> str | None:
    """Return the hostname part of one parsed URL."""
    return urlparse(url).hostname
