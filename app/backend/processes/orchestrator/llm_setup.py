"""LLM provider initialization and configuration for the orchestrator.

Loads LLM credentials from the database, builds the appropriate provider
client (Ollama, OpenRouter, or custom), and wires context settings into
the gateway orchestrator. Handles provider lookup order and model identity.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .factories import (
    make_evidence_bundle_fn,
    make_graph_facts_fn,
    make_memory_route_diagnostic_fn,
    make_save_fn,
    make_save_record_fn,
    make_zone_context_fn,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from zoneinfo import ZoneInfo

    from backend.config import Settings
    from backend.llm.capabilities_registry import CapabilitiesRegistry

    from .client import DBProxyClient


logger = logging.getLogger(__name__)

CUSTOM_API_PREFIX = "/v1"


def _build_gateway_orchestrator(
    *,
    broker: object,
    concurrency: object,
    db_client: DBProxyClient,
    user_profile: dict[str, str],
    user_tz: ZoneInfo | None,
) -> object:
    """Build the gateway orchestrator with its DB-backed helpers.

    Note: ``context_fn`` is constructed here as a placeholder without a
    capabilities resolver because the LLM client does not yet exist at
    this point in startup. ``_apply_llm_context_settings`` overwrites it
    with a fully wired version once ``_init_llm`` has built the client.
    """
    from backend.agent.agent_loop import AgentOrchestrator

    return AgentOrchestrator(
        broker=broker,
        concurrency=concurrency,
        context_fn=make_zone_context_fn(
            db_client,
            capabilities_resolver=None,
        ),
        evidence_bundle_fn=make_evidence_bundle_fn(db_client),
        graph_facts_fn=make_graph_facts_fn(db_client),
        save_fn=make_save_fn(db_client),
        save_record_fn=make_save_record_fn(db_client),
        write_memory_route_diagnostic_fn=make_memory_route_diagnostic_fn(
            db_client,
        ),
        user_profile=user_profile,
        list_skills_fn=db_client.list_skills,
        search_skills_fn=db_client.search_skills,
        create_skill_fn=db_client.create_skill,
        log_skill_usage_fn=db_client.log_skill_usage,
        turn_store=db_client,
    )


def _build_runtime_orchestrator(
    *,
    broker: object,
    db_client: DBProxyClient,
    user_profile: dict[str, str],
    user_tz: ZoneInfo | None,
    gateway_concurrency_cls: object,
) -> object:
    """Build the gateway orchestrator used by this process runtime."""
    return _build_gateway_orchestrator(
        broker=broker,
        concurrency=gateway_concurrency_cls(),
        db_client=db_client,
        user_profile=user_profile,
        user_tz=user_tz,
    )


def _build_capabilities_resolver(
    *,
    capabilities_registry: CapabilitiesRegistry,
    llm_client: object,
) -> Callable[[], Awaitable[object]]:
    """Build a closure that resolves capabilities for the active LLM client.

    The closure goes through the registry so the result is cached for the
    rest of the process lifetime. Both arguments are required: the function
    raises if the client lacks the duck-typed surface (``name`` and
    ``model``) instead of silently returning None.
    """
    if not hasattr(llm_client, "model") or not hasattr(llm_client, "name"):
        raise TypeError(
            "_build_capabilities_resolver requires an llm_client exposing "
            "'name' and 'model'; got "
            f"{type(llm_client).__name__}",
        )

    async def resolve() -> object:
        return await capabilities_registry.get_or_detect(
            llm_client,
            llm_client.model,
        )

    return resolve


def _apply_llm_context_settings(
    *,
    orch: object,
    db_client: DBProxyClient,
    llm_config: dict[str, object] | None,
    user_tz: ZoneInfo | None,
    capabilities_registry: CapabilitiesRegistry,
    llm_client: object,
) -> None:
    """Apply LLM-specific context settings to the gateway orchestrator."""
    if llm_config is None:
        return
    orch.model_name = llm_config.get("model", "")
    logger.info("Model identity set: %r", orch.model_name)
    resolver = _build_capabilities_resolver(
        capabilities_registry=capabilities_registry,
        llm_client=llm_client,
    )
    # Stash on the orchestrator so _apply_runtime_context_fn can rebuild
    # context_fn later (during dialog recorder bootstrap) with the same
    # resolver attached to the main chat model.
    orch._capabilities_resolver = resolver
    orch.context_fn = make_zone_context_fn(
        db_client,
        capabilities_resolver=resolver,
    )


def _apply_runtime_context_fn(
    *,
    orchestrator: object,
    db_client: DBProxyClient,
    llm_config: dict[str, object],
    embed_fn: Callable[[str], Awaitable[list[float]]] | None,
    compression_llm_call: Callable | None = None,
) -> None:
    """Apply the final context function used by the runtime orchestrator.

    Reads the capabilities resolver previously stashed by
    ``_apply_llm_context_settings``. Direct attribute access — if missing,
    AttributeError is raised loudly so the missing wiring is obvious.
    """
    del llm_config
    # Hand the (vector-ready-gated) query embedder to the recall tool so
    # memory_recall can run hybrid FTS+vector search. None when no
    # embedding model is configured — recall then stays FTS+graph only.
    from backend.tools.handlers.registration import set_recall_embed_fn

    set_recall_embed_fn(embed_fn)
    resolver = orchestrator._capabilities_resolver
    orchestrator.context_fn = make_zone_context_fn(
        db_client,
        capabilities_resolver=resolver,
        compression_llm_call=compression_llm_call,
    )


async def _init_llm(
    db_client: DBProxyClient,
    settings: Settings,
    broker: object = None,
    capabilities_registry: CapabilitiesRegistry | None = None,
) -> tuple[
    Callable[..., Awaitable[object]] | None,
    dict[str, object] | None,
    str | None,
    object | None,
]:
    """Seed env config, load LLM credentials from DB, create LLM client.

    Args:
        db_client: Connected DBProxyClient.
        settings: Application settings.
        broker: Optional ToolBroker for OpenAI tool schemas.
        capabilities_registry: Process-local capabilities cache. Injected
            into the Ollama client at construction so chat requests can
            resolve the model context window before each call.

    Returns:
        (llm_call, llm_config, active_service_type, llm_client) —
        ``llm_call`` and ``llm_client`` are None when no provider is
        configured. The raw client is returned so the caller can wire a
        capabilities resolver into the orchestrator's context_fn.
    """
    from backend.llm.call_factory import make_llm_call
    from backend.llm.custom_provider.client import CustomLLMClient
    from backend.llm.ollama.client import OllamaClient
    from backend.llm.openrouter.client import OpenRouterClient

    from .env_config_loader import seed_bot_configs_from_env, seed_llm_config_from_env

    await seed_llm_config_from_env(db_client)
    await seed_bot_configs_from_env(db_client)

    active_service_type, llm_config = await _load_active_llm_config(db_client)

    if llm_config is None:
        logger.critical(
            "No active LLM integration found. "
            "Orchestrator starting in degraded mode — chat unavailable. "
            "Configure a provider at /admin/llm, then restart."
        )
        return None, None, None, None

    llm_client = _build_llm_client(
        active_service_type=active_service_type,
        llm_config=llm_config,
        settings=settings,
        custom_client_cls=CustomLLMClient,
        openrouter_client_cls=OpenRouterClient,
        ollama_client_cls=OllamaClient,
        capabilities_registry=capabilities_registry,
    )

    logger.info("LLM client initialized from DB (provider=%s)", active_service_type)
    tools = broker.list_openai_schemas() if broker is not None else None
    llm_call = make_llm_call(llm_client, tools=tools)
    return llm_call, llm_config, active_service_type, llm_client


def _build_llm_service_lookup_order() -> list[str]:
    """Return the provider lookup order, with env override first when set."""
    env_provider = os.environ.get("BIMEMO_LLM_PROVIDER")
    llm_service_types = ["openrouter", "ollama", "custom"]
    if env_provider and env_provider in llm_service_types:
        llm_service_types.remove(env_provider)
        llm_service_types.insert(0, env_provider)
    return llm_service_types


async def _load_active_llm_config(
    db_client: DBProxyClient,
) -> tuple[str | None, dict[str, object] | None]:
    """Load the first active LLM config using the configured lookup order."""
    for service_type in _build_llm_service_lookup_order():
        result = await db_client.get_integration_config(service_type)
        if result.get("integration") is not None:
            return service_type, result["credentials"]
    return None, None


def _build_llm_client(
    *,
    active_service_type: str,
    llm_config: dict[str, object],
    settings: Settings,
    custom_client_cls: object,
    openrouter_client_cls: object,
    ollama_client_cls: object,
    capabilities_registry: CapabilitiesRegistry | None = None,
) -> object:
    """Build the concrete LLM client for the selected provider."""
    from backend.llm.capabilities import CapabilitySupport

    from .process_helpers import parse_optional_int

    if active_service_type == "openrouter":
        client = openrouter_client_cls(
            api_key=llm_config.get("api_key", ""),
            model=llm_config.get("model", ""),
            base_url=llm_config.get("base_url", "https://openrouter.ai"),
            num_ctx=parse_optional_int(llm_config.get("num_ctx")),
        )
    elif active_service_type == "custom":
        client = custom_client_cls(
            api_key=llm_config.get("api_key", ""),
            model=llm_config.get("model", ""),
            base_url=llm_config.get("base_url", ""),
            api_prefix=CUSTOM_API_PREFIX,
            num_ctx=parse_optional_int(llm_config.get("num_ctx")),
        )

    else:
        from backend.llm.compute_config import default_compute_config
        from backend.llm.hardware import detect_hardware, select_profile

        hardware = detect_hardware()
        profile = select_profile(hardware)
        client = ollama_client_cls(
            base_url=llm_config.get("base_url", "http://ollama:11434"),
            dialog_recorder_base_url=llm_config.get("dialog_recorder_base_url") or None,
            model=llm_config.get("model", profile.primary_model),
            token_path="/run/ollama-token/token",  # noqa: S106
            num_ctx=parse_optional_int(llm_config.get("num_ctx")),
            profile_config=profile,
            compute_config=default_compute_config(),
            capabilities_registry=capabilities_registry,
        )
    client.structured_capability_declarations = {
        "native_tools": CapabilitySupport(settings.llm_native_tools_support),
        "exact_named_choice": CapabilitySupport(
            settings.llm_exact_named_choice_support
        ),
        "required_native_decision": CapabilitySupport(
            settings.llm_required_native_decision_support
        ),
        "non_streaming_custom_schema": CapabilitySupport(
            settings.llm_custom_schema_support
        ),
    }
    return client
