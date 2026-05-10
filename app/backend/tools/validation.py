"""Parameter validation for tool calls.

Provides path-traversal detection, URL-scheme allowlist, and SSRF
prevention via resolve-first validation. AssistantPolicy toggles
(loopback / RFC1918 / CGNAT / link-local / sensitive paths /
HTTPS-only) are read from AssistantPolicy every call so a
runtime policy update takes effect immediately.

A small set of networks and patterns stays always-on: the
cloud-metadata IP (169.254.169.254), the zero-network
(0.0.0.0/8), the IETF benchmark range (198.18.0.0/15), the
IPv4-mapped IPv6 range (::ffff:0:0/96), numeric-IP obfuscation
formats, and the path-traversal pattern.
"""

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

from backend.security.policy import get_policy


logger = logging.getLogger(__name__)


# --- Always-on always-blocked networks ---
_ALWAYS_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("169.254.169.254/32"),  # cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),  # this network / unspecified
    ipaddress.ip_network("198.18.0.0/15"),  # IETF benchmark
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
)

# --- Toggle-driven network groups ---
_LOOPBACK_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)
_RFC1918_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_CGNAT_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("100.64.0.0/10"),
)
_LINK_LOCAL_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)

# --- Always-on regex patterns ---
_NUMERIC_IP_RE = re.compile(r"^(0x[0-9a-fA-F]+|0[0-7]+|\d{8,})$")
_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")

# --- Always-on private-key paths (locked critical) ---
_PRIVATE_KEY_PATHS: frozenset[str] = frozenset({".ssh", ".gnupg"})

# --- Always-allowed URL schemes (HTTPS is permanent) ---
_ALWAYS_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})

# --- Catalog keys read from AssistantPolicy ---
_KEY_BLOCK_LOOPBACK = "network.block_loopback"
_KEY_BLOCK_RFC1918 = "network.block_rfc1918"
_KEY_BLOCK_CGNAT = "network.block_cgnat"
_KEY_BLOCK_LINK_LOCAL = "network.block_link_local"
_KEY_HTTPS_ONLY = "network.https_only"
_KEY_HTTP_ALLOWED_HOSTS = "network.http_allowed_hosts"
_KEY_DNS_TIMEOUT_SECONDS = "network.dns_timeout_seconds"
_KEY_DENY_READ_DOTFILES = "filesystem.paths.deny_read_dotfiles"
_KEY_DENY_WRITE_DOTFILES = "filesystem.paths.deny_write_dotfiles"
_KEY_CUSTOM_DENY_LIST = "filesystem.paths.custom_deny_list"

# --- Default dotfile names disabled by deny_*_dotfiles toggles ---
_DOTFILE_NAMES: frozenset[str] = frozenset(
    {".bashrc", ".profile", ".bash_profile", ".env", ".config"}
)


class ValidationError(Exception):
    """Raised when parameter validation fails."""


def _validation_error(message: str) -> ValidationError:
    """Build one validation error with a plain message."""
    return ValidationError(message)


def _active_blocked_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Build the active blocked-networks list from AssistantPolicy."""
    resolver = get_policy()
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = list(
        _ALWAYS_BLOCKED_NETWORKS,
    )
    if resolver.get_bool(_KEY_BLOCK_LOOPBACK):
        networks.extend(_LOOPBACK_NETWORKS)
    if resolver.get_bool(_KEY_BLOCK_RFC1918):
        networks.extend(_RFC1918_NETWORKS)
    if resolver.get_bool(_KEY_BLOCK_CGNAT):
        networks.extend(_CGNAT_NETWORKS)
    if resolver.get_bool(_KEY_BLOCK_LINK_LOCAL):
        networks.extend(_LINK_LOCAL_NETWORKS)
    return networks


def _active_write_block_paths() -> frozenset[str]:
    """Return path components blocked for write by AssistantPolicy."""
    resolver = get_policy()
    blocked: set[str] = set(_PRIVATE_KEY_PATHS)
    if resolver.get_bool(_KEY_DENY_WRITE_DOTFILES):
        blocked.update(_DOTFILE_NAMES)
    blocked.update(resolver.get_list_str(_KEY_CUSTOM_DENY_LIST))
    return frozenset(blocked)


def _active_read_block_paths() -> frozenset[str]:
    """Return path components blocked for read by AssistantPolicy."""
    resolver = get_policy()
    blocked: set[str] = set(_PRIVATE_KEY_PATHS)
    if resolver.get_bool(_KEY_DENY_READ_DOTFILES):
        blocked.update(_DOTFILE_NAMES)
    blocked.update(resolver.get_list_str(_KEY_CUSTOM_DENY_LIST))
    return frozenset(blocked)


def _allowed_url_schemes() -> frozenset[str]:
    """Return the URL-scheme allowlist from AssistantPolicy."""
    resolver = get_policy()
    if resolver.get_bool(_KEY_HTTPS_ONLY):
        return _ALWAYS_ALLOWED_SCHEMES
    return _ALWAYS_ALLOWED_SCHEMES | frozenset({"http"})


def _http_host_allowed(hostname: str) -> bool:
    """Return whether one hostname is in the active HTTP allowlist."""
    resolver = get_policy()
    allowlist = resolver.get_list_str(_KEY_HTTP_ALLOWED_HOSTS)
    return hostname in allowlist


def _dns_timeout_seconds() -> float:
    """Return the active DNS resolution timeout in seconds."""
    return get_policy().get_float(_KEY_DNS_TIMEOUT_SECONDS)


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
    """Check for sensitive components in a write path.

    Args:
        path: File path to validate.

    Raises:
        ValidationError: If a write-blocked component is present.
    """
    blocked = _active_write_block_paths()
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if part in blocked:
            raise _validation_error(f"Write to sensitive path blocked: {part}")


def check_sensitive_read_path(path: str) -> None:
    """Check for sensitive components in a read path.

    Args:
        path: File path to validate.

    Raises:
        ValidationError: If a read-blocked component is present.
    """
    blocked = _active_read_block_paths()
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if part in blocked:
            raise _validation_error(f"Read from sensitive path blocked: {part}")


def check_url_scheme(url: str) -> None:
    """Validate URL uses a scheme allowed by AssistantPolicy.

    Args:
        url: URL to validate.

    Raises:
        ValidationError: If the scheme is not allowed by AssistantPolicy.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _allowed_url_schemes():
        raise _validation_error(f"URL scheme not allowed: {parsed.scheme}")
    if parsed.scheme == "http":
        hostname = parsed.hostname or ""
        if not _http_host_allowed(hostname):
            raise _validation_error(
                f"HTTP host not in allowlist: {hostname}",
            )


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
    """Resolve one hostname with the active DNS timeout."""
    socket.setdefaulttimeout(_dns_timeout_seconds())
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
    for network in _active_blocked_networks():
        if addr in network:
            return network
    return None


def _resolved_ip_or_error(hostname: str) -> str:
    """Resolve one hostname and return the first IP or a validation error."""
    results = _dns_lookup(hostname)
    return _first_dns_ip(hostname, results)


def resolve_and_check(hostname: str) -> str:
    """Resolve hostname and check against active blocked ranges.

    Args:
        hostname: Hostname to resolve.

    Returns:
        Resolved IP address string for pinning.

    Raises:
        ValidationError: If resolved IP is in a blocked range.
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
