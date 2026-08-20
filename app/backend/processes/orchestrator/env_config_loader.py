"""Environment variable seeding for LLM and bot configurations.

Reads LLM provider credentials and messenger bot tokens from environment
variables and persists them into the database config store. Runs during
orchestrator startup to allow configuration via environment without the Admin UI.
"""

import logging
import os
from typing import TYPE_CHECKING

from backend.constants import (
    ENV_ALLOWED_USER_IDS_SUFFIX,
    ENV_BOT_TOKEN_SUFFIX,
    ENV_DISCORD_PREFIX,
    ENV_LLM_PROVIDER,
    ENV_PREFIX_BY_PROVIDER,
    ENV_TELEGRAM_PREFIX,
)


if TYPE_CHECKING:
    from .client import DBProxyClient


logger = logging.getLogger(__name__)


async def seed_llm_config_from_env(db_client: DBProxyClient) -> None:
    """Write LLM config from env vars into DB, overwriting existing."""
    provider = os.environ.get(ENV_LLM_PROVIDER)
    if not provider:
        return

    prefix = ENV_PREFIX_BY_PROVIDER.get(provider)
    if not prefix:
        logger.warning("Unknown BIMEMO_LLM_PROVIDER=%r, skipping env seed", provider)
        return

    # NOTE: BIMEMO_*_NUM_CTX is intentionally NOT seeded into DB
    # credentials. The Ollama provider reads it directly via
    # _read_env_override(), keeping env-override and UI-override as
    # independent sources in the priority chain
    # (env > UI > auto-detect > hard error).
    credentials = _collect_env_credentials(
        env_prefix=prefix,
        field_map={
            "API_KEY": "api_key",
            "MODEL": "model",
            "DIALOG_RECORDER_MODEL": "dialog_recorder_model",
            "BASE_URL": "base_url",
            "DIALOG_RECORDER_BASE_URL": "dialog_recorder_base_url",
            "EMBEDDING_BASE_URL": "embedding_base_url",
            "EMBEDDING_MODEL": "embedding_model",
        },
    )

    if not credentials:
        logger.debug("No %s* env vars found, skipping env seed", prefix)
        return

    await _store_integration_credentials(
        db_client=db_client,
        service_type=provider,
        credentials=credentials,
    )
    logger.info(
        "LLM config seeded from env vars (provider=%s, fields=%s)",
        provider,
        list(credentials.keys()),
    )


async def seed_bot_configs_from_env(db_client: DBProxyClient) -> None:
    """Write Telegram/Discord bot credentials from env vars into DB.

    Each bot is seeded independently — if the BOT_TOKEN env var is set,
    the integration is created/overwritten in DB via store_llm_config.
    """
    bot_configs = [
        {
            "service_type": "telegram",
            "env_prefix": ENV_TELEGRAM_PREFIX,
            "field_map": {
                ENV_BOT_TOKEN_SUFFIX: "bot_token",
                ENV_ALLOWED_USER_IDS_SUFFIX: "allowed_user_ids",
            },
            "required_env": ENV_TELEGRAM_PREFIX + ENV_BOT_TOKEN_SUFFIX,
        },
        {
            "service_type": "discord",
            "env_prefix": ENV_DISCORD_PREFIX,
            "field_map": {
                ENV_BOT_TOKEN_SUFFIX: "bot_token",
                ENV_ALLOWED_USER_IDS_SUFFIX: "allowed_user_ids",
            },
            "required_env": ENV_DISCORD_PREFIX + ENV_BOT_TOKEN_SUFFIX,
        },
    ]

    for bot in bot_configs:
        # Only seed if the required env var (bot token) is present
        if not os.environ.get(bot["required_env"]):
            continue

        credentials = _collect_env_credentials(
            env_prefix=bot["env_prefix"],
            field_map=bot["field_map"],
        )

        if not credentials:
            continue

        await _store_integration_credentials(
            db_client=db_client,
            service_type=bot["service_type"],
            credentials=credentials,
        )
        logger.info(
            "%s config seeded from env vars (fields=%s)",
            bot["service_type"].capitalize(),
            list(credentials.keys()),
        )


def _collect_env_credentials(
    *,
    env_prefix: str,
    field_map: dict[str, str],
) -> dict[str, str]:
    """Collect configured credentials from one env-prefix mapping."""
    credentials: dict[str, str] = {}
    for env_suffix, credential_key in field_map.items():
        value = os.environ.get(env_prefix + env_suffix)
        if value:
            credentials[credential_key] = value
    return credentials


async def _store_integration_credentials(
    *,
    db_client: DBProxyClient,
    service_type: str,
    credentials: dict[str, str],
) -> None:
    """Persist one integration credential bundle into the DB config store."""
    await db_client.send_operation(
        "store_llm_config",
        {
            "service_type": service_type,
            "credentials": credentials,
        },
    )
