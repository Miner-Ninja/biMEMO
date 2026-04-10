"""Credential Proxy IPC client for credential and integration operations.

Routes credential and integration operations through the Credential
Proxy process, which relays to DB Proxy Socket C. Handles integration
CRUD, LLM configuration management, and credential storage with
encryption at the proxy boundary.
"""

from __future__ import annotations

from typing import Any

from backend.processes.fastapi_app.base_client import _BaseIPCClient


class CredentialClient(_BaseIPCClient):
    """Typed async client for Credential Proxy (credential operations).

    Routes credential and integration operations through the
    Credential Proxy process, which relays to DB Proxy Socket C.
    """

    async def list_integrations(self) -> list[dict[str, Any]]:
        """List all integrations."""
        result = await self.send_operation("list_integrations")
        return result.get("integrations", [])

    async def create_integration(
        self,
        name: str,
        integration_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new integration."""
        return await self.send_operation(
            "create_integration",
            {
                "name": name,
                "service_type": integration_type,
                "config": config,
            },
        )

    async def delete_integration(self, name: str) -> dict[str, Any]:
        """Delete an integration by name."""
        return await self.send_operation(
            "delete_integration",
            {
                "name": name,
            },
        )

    async def store_llm_config(
        self,
        service_type: str,
        credentials: dict[str, str],
    ) -> dict[str, Any]:
        """Atomically upsert an LLM provider integration with credentials."""
        return await self.send_operation(
            "store_llm_config",
            {
                "service_type": service_type,
                "credentials": credentials,
            },
        )

    async def get_llm_config_meta(self) -> dict[str, Any]:
        """Return LLM provider type, model, base URL (no secrets)."""
        return await self.send_operation("get_llm_config_meta", {})

    async def list_llm_configs(self) -> dict[str, Any]:
        """Return all LLM configurations with metadata (no secrets)."""
        return await self.send_operation("list_llm_configs", {})

    async def create_llm_config(
        self,
        service_type: str,
        credentials: dict[str, str],
    ) -> dict[str, Any]:
        """Create a new LLM configuration (inactive by default)."""
        return await self.send_operation(
            "create_llm_config",
            {
                "service_type": service_type,
                "credentials": credentials,
            },
        )

    async def update_llm_config(
        self,
        config_id: str,
        credentials: dict[str, str],
    ) -> dict[str, Any]:
        """Update credentials for an existing LLM configuration."""
        return await self.send_operation(
            "update_llm_config",
            {
                "config_id": config_id,
                "credentials": credentials,
            },
        )

    async def delete_llm_config(self, config_id: str) -> dict[str, Any]:
        """Delete an LLM configuration and its credentials."""
        return await self.send_operation(
            "delete_llm_config",
            {
                "config_id": config_id,
            },
        )

    async def activate_llm_config(self, config_id: str) -> dict[str, Any]:
        """Set an LLM configuration as active (deactivate others)."""
        return await self.send_operation(
            "activate_llm_config",
            {
                "config_id": config_id,
            },
        )

    async def get_llm_config_full(self, config_id: str) -> dict[str, Any]:
        """Return full LLM config including secrets (for testing)."""
        return await self.send_operation(
            "get_llm_config_full",
            {
                "config_id": config_id,
            },
        )
