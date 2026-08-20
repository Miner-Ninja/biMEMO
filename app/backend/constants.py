"""Shared protocol constants for the biMEMO backend.

Protocol versions, channel names, id/key prefixes, and environment
variable names used across processes. This module has no imports: it is
safe to import from any module without circular-import risk.

These values form part of the persistence and IPC contract. Changing a
value is a protocol event (stored rows keep the old value), not a rename.
"""

# ruff: noqa: S105

# --- Protocol versions ---------------------------------------------------

#: Tool-permission policy version written on turns, invocations, and grants.
TOOL_POLICY_VERSION = "registry-v1"

# --- Channels ------------------------------------------------------------

#: Web UI entry point.
CHANNEL_WEB = "web"
#: Scheduler-driven runs.
CHANNEL_SCHEDULER = "scheduler"
#: Prefix of the per-provider bridge channels (``bridge:<provider>``).
BRIDGE_CHANNEL_PREFIX = "bridge:"
#: ``meta["source"]`` stamped on messages that arrived via a chat bridge.
EXTERNAL_CHAT_BRIDGE_SOURCE = "external_chat_bridge"

# --- Id / key prefixes ----------------------------------------------------

#: Prefix of scheduler-owned conversation/session/request ids.
SCHED_ID_PREFIX = "sched_"
#: Prefix of synthetic outbound bridge message ids created by the scheduler.
SCHED_MESSAGE_ID_PREFIX = "sched_msg_"
#: Prefix of pending bridge message-link external message ids.
PENDING_LINK_PREFIX = "pending:"

#: uuid5 namespace roots for deterministic id derivation.
UUID_NAMESPACE_BRIDGE = "bimemo:bridge:"
UUID_NAMESPACE_MCP = "bimemo:mcp:"
UUID_NAMESPACE_AGENT_TURN = "bimemo-agent-turn:"

#: Evidence-key prefixes on tool obligations.
EVIDENCE_KEY_TOOL_PREFIX = "tool:"
EVIDENCE_KEY_URL_PREFIX = "url:"

# --- Environment variable names -------------------------------------------

ENV_LLM_PROVIDER = "BIMEMO_LLM_PROVIDER"
ENV_ADMIN_PASSWORD = "BIMEMO_ADMIN_PASSWORD"
ENV_RUNTIME_DIR = "BIMEMO_RUNTIME_DIR"
ENV_CA_ROOT_PATH = "BIMEMO_CA_ROOT_PATH"

#: Per-provider ``num_ctx`` overrides (read directly, never seeded into DB).
ENV_OLLAMA_NUM_CTX = "BIMEMO_OLLAMA_NUM_CTX"
ENV_CUSTOM_NUM_CTX = "BIMEMO_CUSTOM_NUM_CTX"
ENV_OPENROUTER_NUM_CTX = "BIMEMO_OPENROUTER_NUM_CTX"

#: Prefix of the per-label MCP token variables (``BIMEMO_MCP_TOKEN_<label>``).
ENV_MCP_TOKEN_PREFIX = "BIMEMO_MCP_TOKEN_"

#: Per-provider env prefixes used by the LLM config env seeder.
ENV_PREFIX_BY_PROVIDER = {
    "openrouter": "BIMEMO_OPENROUTER_",
    "ollama": "BIMEMO_OLLAMA_",
    "custom": "BIMEMO_CUSTOM_",
}

ENV_TELEGRAM_PREFIX = "BIMEMO_TELEGRAM_"
ENV_DISCORD_PREFIX = "BIMEMO_DISCORD_"
ENV_BOT_TOKEN_SUFFIX = "BOT_TOKEN"
ENV_ALLOWED_USER_IDS_SUFFIX = "ALLOWED_USER_IDS"
