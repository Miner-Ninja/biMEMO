"""Shared redaction and masking utilities for channel adapters.

Provides parameter masking for sensitive data (emails, URLs,
paths, message bodies) and confirmation message formatting
used by Telegram, Discord, and other channel adapters.
"""

import re
from dataclasses import dataclass


def _masked_name(name: str) -> str:
    """Mask one name-like token while keeping a short readable prefix."""
    if len(name) > 3:
        return name[:2] + "****" + name[-1]
    return name


def mask_email(email: str) -> str:
    """Mask an email address for display.

    Example: user@example.com -> us**@ex****e.com

    Args:
        email: Email address to mask.

    Returns:
        Masked email string.
    """
    parts = email.split("@")
    if len(parts) != 2:
        return email
    local = parts[0]
    domain = parts[1]
    masked_local = local[:2] + "**" if len(local) > 2 else local
    domain_parts = domain.split(".")
    domain_parts[0] = _masked_name(domain_parts[0])
    masked_domain = ".".join(domain_parts)
    return f"{masked_local}@{masked_domain}"


def mask_url(url: str) -> str:
    """Mask a URL for display, keeping domain only.

    Example: https://example.com/path -> https://ex****e.com/***

    Args:
        url: URL to mask.

    Returns:
        Masked URL string.
    """
    match = re.match(r"(https?://)" r"([^/]+)" r"(.*)", url)
    if not match:
        return url
    scheme = match.group(1)
    domain = match.group(2)
    domain_parts = domain.split(".")
    domain_parts[0] = _masked_name(domain_parts[0])
    masked_domain = ".".join(domain_parts)
    return f"{scheme}{masked_domain}/***"


def mask_path(path: str) -> str:
    """Mask a file path for display.

    Example: /home/user/secret.txt -> /home/u***/se***t.txt

    Args:
        path: File path to mask.

    Returns:
        Masked path string.
    """
    parts = path.split("/")
    masked = []
    for part in parts:
        if not part:
            masked.append(part)
        elif len(part) > 3:
            masked.append(part[:2] + "***" + part[-1])
        else:
            masked.append(part)
    return "/".join(masked)


def mask_body(body: str) -> str:
    """Mask a message body for display.

    Shows first 10 characters then masked indicator.

    Args:
        body: Body text to mask.

    Returns:
        Masked body string.
    """
    if len(body) <= 10:
        return body
    return body[:10] + "[...masked...]"


def _mask_parameter(key: str, value: str) -> str:
    """Mask a parameter value based on its key name.

    Args:
        key: Parameter name.
        value: Parameter value.

    Returns:
        Masked value string.
    """
    key_lower = key.lower()
    if "email" in key_lower or "@" in value:
        return mask_email(value)
    if "url" in key_lower or value.startswith("http"):
        return mask_url(value)
    if "path" in key_lower or "/" in value:
        return mask_path(value)
    if "body" in key_lower or "content" in key_lower:
        return mask_body(value)
    return mask_body(value)


def _parameter_line(key: str, value: object) -> str:
    """Return one masked parameter line for a confirmation message."""
    masked = _mask_parameter(key, str(value))
    return f"  {key}: {masked}"


@dataclass(frozen=True)
class ConfirmationMessage:
    """Formatted confirmation message for channel display.

    Attributes:
        text: Formatted message text with masked params.
        action_id: Cryptographic action identifier.
        tool_name: Name of the tool requiring confirmation.
    """

    text: str
    action_id: str
    tool_name: str


def format_confirmation(
    action_id: str,
    tool_name: str,
    parameters: dict,
) -> ConfirmationMessage:
    """Format a trust confirmation for channel display.

    Masks sensitive parameter values and creates
    a user-friendly confirmation message.

    Args:
        action_id: Cryptographic action identifier.
        tool_name: Name of the RED zone tool.
        parameters: Tool parameters to display.

    Returns:
        ConfirmationMessage with masked parameters.
    """
    lines = [
        f"\U0001f534 Confirmation required: {tool_name}",
        "",
    ]
    for key, value in parameters.items():
        lines.append(_parameter_line(key, value))
    lines.extend(
        [
            "",
            "View full details in Web UI.",
            "",
            "Reply: Approve / Deny",
        ]
    )
    return ConfirmationMessage(
        text="\n".join(lines),
        action_id=action_id,
        tool_name=tool_name,
    )
