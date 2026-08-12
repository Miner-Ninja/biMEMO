"""Single assistant policy used by runtime safety checks.

The application has one operating mode. These values are intentionally
static so normal runtime behavior does not depend on user-selectable
security modes.
"""

from __future__ import annotations


PolicyValue = bool | int | float | str | list[str]


DEFAULT_POLICY: dict[str, PolicyValue] = {
    "input_filter.block_code_execution": False,
    "input_filter.block_dangerous_shell_only": True,
    "input_filter.block_prompt_injection": True,
    "input_filter.block_role_play_phrases": False,
    "input_filter.block_credentials_strict": True,
    "input_filter.block_credentials_generic": False,
    "input_filter.block_pii": False,
    "input_filter.flag_high_entropy_input": False,
    "input_filter.max_message_size_bytes": 1_000_000,
    "input_filter.injection_iteration_penalty": True,
    "input_filter.custom_credential_patterns": [],
    "input_filter.custom_injection_patterns": [],
    "output_filter.redact_credentials_in_output": True,
    "output_filter.redact_internal_paths": False,
    "output_filter.block_exfiltration_patterns": False,
    "output_filter.block_exfiltration_strict_only": True,
    "output_filter.block_high_entropy_output": False,
    "output_filter.block_injection_in_output": True,
    "filesystem.paths.deny_read_private_keys": True,
    "filesystem.paths.deny_path_traversal": True,
    "filesystem.paths.deny_read_dotfiles": False,
    "filesystem.paths.deny_write_dotfiles": True,
    "filesystem.paths.custom_deny_list": [],
    "filesystem.uploads.allowed_extensions": [
        ".txt",
        ".md",
        ".pdf",
        ".csv",
        ".json",
        ".xml",
        ".html",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".vue",
        ".svelte",
        ".go",
        ".rs",
        ".cpp",
        ".cc",
        ".cxx",
        ".h",
        ".hpp",
        ".java",
        ".kt",
        ".scala",
        ".rb",
        ".php",
        ".swift",
        ".dart",
        ".lua",
        ".zig",
        ".nim",
        ".ex",
        ".exs",
        ".erl",
        ".clj",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".tf",
        ".tfvars",
        ".hcl",
        ".nix",
        ".lock",
        ".gradle",
        ".sbt",
        ".cmake",
        ".mk",
        ".proto",
        ".graphql",
        ".gql",
        ".patch",
        ".diff",
        ".yaml",
        ".yml",
        ".log",
        ".sh",
        ".sql",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    ],
    "filesystem.uploads.bare_name_allowlist": [
        "Makefile",
        "Dockerfile",
        "Containerfile",
        "Justfile",
        "Rakefile",
        "Gemfile",
        "Pipfile",
        "LICENSE",
        ".gitignore",
        ".dockerignore",
        ".editorconfig",
        ".env.example",
    ],
    "filesystem.uploads.max_size_mb": 50,
    "network.https_only": False,
    "network.http_allowed_hosts": ["localhost", "127.0.0.1", "*.local"],
    "network.block_loopback": False,
    "network.block_rfc1918": False,
    "network.block_cgnat": False,
    "network.block_cloud_metadata": True,
    "network.block_link_local": True,
    "network.block_numeric_ip_formats": True,
    "network.dns_resolve_first": True,
    "network.dns_timeout_seconds": 10.0,
    "network.custom_allowed_hosts": [],
    "network.custom_blocked_hosts": [],
    "network.max_redirects_per_fetch": 5,
    "trust.confirm_http_fetch": False,
    "trust.confirm_file_write": False,
    "trust.confirm_email_send": True,
    "trust.confirm_schedule_task": False,
    "trust.confirmation_timeout_seconds": 1800,
    "trust.confirmation_channel_binding": True,
    "trust.auto_downgrade_threshold": 5,
    "trust.red_zone_tools": ["email_send"],
    "tools.manual_control_enabled": True,
    "tools.reask_enabled": True,
    "tools.reask_max_retries": 1,
    "observability.trace_enabled": True,
    "trust.yellow_zone_tools": [
        "file_write",
        "web_search",
        "schedule_task",
        "http_fetch",
    ],
    "agent_limits.iterations.max": 80,
    "agent_limits.iterations.max_when_injection": 10,
    "agent_limits.loop_timeout_seconds": 1800,
    "agent_limits.tools.file_read_per_loop": 50,
    "agent_limits.tools.file_write_per_loop": 30,
    "agent_limits.tools.file_search_per_loop": 20,
    "agent_limits.tools.web_search_per_loop": 20,
    "agent_limits.tools.http_fetch_per_loop": 20,
    "agent_limits.tools.email_send_per_loop": 1,
    "agent_limits.tools.schedule_task_per_loop": 10,
    "agent_limits.tools.memory_recall_per_loop": 20,
    "agent_limits.tools.total_budget_per_loop": 200,
    "agent_limits.tools.total_budget_when_injection": 50,
    "agent_limits.web_search_window.max_calls": 30,
    "agent_limits.web_search_window.seconds": 60,
    "memory.enable_long_term_memory": True,
    "memory.sanitize_before_save": True,
    "memory.respect_tainted_flag": True,
    "memory.memory_ttl_days": 365,
    "memory.isolate_memory_per_conversation": False,
    "memory.max_memory_records_per_recall": 20,
    "llm.response_schema": "programmer",
    "llm.strict_json_schema": True,
    "llm.system_prompt_addons": (
        "Ты помощник программиста. Отвечай в коде блоках по умолчанию, "
        "ссылайся на файлы по path:line, не извиняйся, не пересказывай diff."
    ),
    "llm.disable_external_tools": False,
    "llm.max_response_tokens": 16_000,
    "auth.require_jwt": True,
    "auth.require_csrf": True,
    "auth.access_token_ttl_minutes": 60,
    "auth.refresh_token_ttl_days": 30,
    "auth.max_concurrent_sessions": 10,
    "auth.session_idle_timeout_minutes": 0,
    "audit.log_red_operations": True,
    "audit.log_blocked_inputs": True,
    "audit.mask_secrets_in_logs": True,
    "audit.audit_retention_days": 30,
    "audit.notify_on_injection": False,
    "frontend.markdown_disallowed_tags": [
        "script",
        "iframe",
        "object",
        "embed",
        "form",
        "style",
        "link",
    ],
    "frontend.csp_unsafe_inline_styles": True,
    "frontend.highlight_red_operations_in_chat": True,
    "frontend.confirm_external_links_click": False,
}


class AssistantPolicy:
    """Typed accessors for the single runtime policy."""

    policy_id = "assistant"

    def __init__(self, settings: dict[str, PolicyValue] | None = None) -> None:
        """Initialize the policy from static settings."""
        self._settings = settings or DEFAULT_POLICY

    def get(
        self,
        key: str,
        default: PolicyValue | None = None,
    ) -> PolicyValue | list[str]:
        """Return one policy value, raising on unknown keys by default."""
        if key in self._settings:
            value = self._settings[key]
            return list(value) if isinstance(value, list) else value
        if default is not None:
            return default
        raise KeyError(f"Unknown assistant policy key: {key!r}")

    def get_bool(self, key: str) -> bool:
        """Return a boolean policy value."""
        value = self.get(key)
        if not isinstance(value, bool):
            raise TypeError(f"Policy key {key!r} is not bool: {type(value).__name__}")
        return value

    def get_int(self, key: str) -> int:
        """Return an integer policy value."""
        value = self.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"Policy key {key!r} is not int: {type(value).__name__}")
        return value

    def get_float(self, key: str) -> float:
        """Return a numeric policy value as float."""
        value = self.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(
                f"Policy key {key!r} is not numeric: {type(value).__name__}"
            )
        return float(value)

    def get_str(self, key: str) -> str:
        """Return a string policy value."""
        value = self.get(key)
        if not isinstance(value, str):
            raise TypeError(f"Policy key {key!r} is not str: {type(value).__name__}")
        return value

    def get_list_str(self, key: str) -> list[str]:
        """Return a list[str] policy value."""
        value = self.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise TypeError(f"Policy key {key!r} is not list[str]")
        return list(value)


_POLICY = AssistantPolicy()


def get_policy() -> AssistantPolicy:
    """Return the process-wide assistant policy."""
    return _POLICY


__all__ = ["DEFAULT_POLICY", "AssistantPolicy", "PolicyValue", "get_policy"]
