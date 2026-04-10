"""Messenger bot startup and lifecycle for the orchestrator process.

Loads bot configurations from the database, starts Telegram and Discord
bots with the appropriate runtime dependencies, and handles graceful
shutdown. Each bot type is started independently based on stored credentials.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from backend.config import parse_configured_id_list

from .bridge_handler import _build_internal_bridge_inbound_url
from .client import DBProxyClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotRuntimeConfig:
    """Loaded bot configuration needed to start one messenger bot."""

    token: str
    allowed_user_ids: frozenset[int]


def _parse_allowed_bot_user_ids(
    raw_value: object,
) -> frozenset[int]:
    """Parse the configured bot allow-list into integer user identifiers."""
    parsed_ids: set[int] = set()
    for raw_id in parse_configured_id_list(str(raw_value or "")):
        try:
            parsed_ids.add(int(raw_id))
        except ValueError:
            logger.warning("Ignoring invalid bot allowed_user_id=%r", raw_id)
    return frozenset(parsed_ids)


async def _start_telegram_bot(
    *,
    token: str,
    allowed_user_ids: frozenset[int],
    orchestrator: object,
    llm_call: Callable,
    stt_client: object | None,
    tts_client: object | None,
    app_state: object,
) -> object:
    """Start the Telegram bot as a bridge transport endpoint."""
    del orchestrator, llm_call, stt_client, tts_client, app_state
    from backend.bot.telegram_bot import TelegramBot
    from backend.channels.telegram import TelegramConfig

    config = TelegramConfig(
        bot_token=token,
        allowed_user_ids=allowed_user_ids,
    )
    bot = TelegramBot(config=config)
    await bot.start_polling()
    return bot


async def _start_discord_bot(
    *,
    token: str,
    allowed_user_ids: frozenset[int],
    orchestrator: object,
    llm_call: Callable,
    stt_client: object | None,
    tts_client: object | None,
    app_state: object,
) -> object:
    """Start the Discord bot as a bridge transport endpoint."""
    del orchestrator, llm_call, stt_client, tts_client, app_state
    from backend.bot.discord_bot import DiscordBot
    from backend.channels.discord import DiscordConfig
    from backend.config import get_settings

    settings = get_settings()
    config = DiscordConfig(
        bot_token=token,
        allowed_user_ids=allowed_user_ids,
        bridge_category_id=settings.discord_bridge_category_id,
        bridge_external_space_id=settings.discord_bridge_server_id,
        bridge_inbound_url=_build_internal_bridge_inbound_url(settings),
        bridge_inbound_token=settings.discord_bridge_token,
    )
    bot = DiscordBot(config=config)
    await bot.start()
    return bot


async def _load_bot_runtime_config(
    *,
    db_client: DBProxyClient,
    service_type: str,
) -> BotRuntimeConfig | None:
    """Load the token and allow-list for one configured bot."""
    result = await db_client.get_integration_config(service_type)
    credentials = result.get("credentials", {})
    token = credentials.get("bot_token")
    if not token:
        logger.info("%s not configured. Add via Admin UI.", service_type.capitalize())
        return None
    return BotRuntimeConfig(
        token=token,
        allowed_user_ids=_parse_allowed_bot_user_ids(
            credentials.get("allowed_user_ids", "")
        ),
    )


async def _launch_configured_bot(
    *,
    service_type: str,
    config: BotRuntimeConfig,
    orchestrator: object,
    llm_call: Callable,
    stt_client: object | None,
    tts_client: object | None,
    app_state: object,
) -> object:
    """Launch one concrete bot from the already loaded runtime config."""
    if service_type == "telegram":
        return await _start_telegram_bot(
            token=config.token,
            allowed_user_ids=config.allowed_user_ids,
            app_state=app_state,
            orchestrator=orchestrator,
            llm_call=llm_call,
            stt_client=stt_client,
            tts_client=tts_client,
        )
    return await _start_discord_bot(
        token=config.token,
        allowed_user_ids=config.allowed_user_ids,
        app_state=app_state,
        orchestrator=orchestrator,
        llm_call=llm_call,
        stt_client=stt_client,
        tts_client=tts_client,
    )


async def _start_configured_bots(
    *,
    db_client: DBProxyClient,
    orchestrator: object,
    llm_call: Callable | None,
    stt_client: object | None,
    tts_client: object | None,
) -> tuple[object | None, object | None]:
    """Start the configured messenger bots when an LLM is available."""
    if llm_call is None:
        return None, None

    started_bots = await _start_runtime_bots(
        db_client=db_client,
        orchestrator=orchestrator,
        llm_call=llm_call,
        stt_client=stt_client,
        tts_client=tts_client,
        service_types=("telegram", "discord"),
    )
    return started_bots["telegram"], started_bots["discord"]


async def _start_runtime_bots(
    *,
    db_client: DBProxyClient,
    orchestrator: object,
    llm_call: Callable,
    stt_client: object | None,
    tts_client: object | None,
    service_types: tuple[str, ...],
) -> dict[str, object | None]:
    """Start the requested messenger bot types and return them by service name."""
    started_bots: dict[str, object | None] = {}
    for service_type in service_types:
        started_bots[service_type] = await _start_bot(
            db_client,
            orchestrator,
            llm_call,
            service_type,
            stt_client=stt_client,
            tts_client=tts_client,
        )
    return started_bots


async def _start_bot(
    db_client: DBProxyClient,
    orchestrator: object,
    llm_call: Callable,
    service_type: str,
    stt_client: object | None = None,
    tts_client: object | None = None,
) -> object | None:
    """Load bot config from DB and start polling if configured.

    Args:
        db_client: Connected DBProxyClient.
        orchestrator: AgentOrchestrator instance.
        llm_call: LLM call function.
        service_type: Bot type — ``"telegram"`` or ``"discord"``.
        stt_client: Optional speech-to-text client for voice input.
        tts_client: Optional text-to-speech client for voice playback.

    Returns:
        Bot instance or None.
    """
    config = await _load_bot_runtime_config(
        db_client=db_client,
        service_type=service_type,
    )
    if config is None:
        return None

    try:
        bot = await _start_loaded_bot_config(
            service_type=service_type,
            config=config,
            orchestrator=orchestrator,
            llm_call=llm_call,
            stt_client=stt_client,
            tts_client=tts_client,
        )
        logger.info("%s bot started from DB config", service_type.capitalize())
        return bot
    except Exception:
        logger.error("Failed to start %s bot", service_type, exc_info=True)
        return None


async def _start_loaded_bot_config(
    *,
    service_type: str,
    config: BotRuntimeConfig,
    orchestrator: object,
    llm_call: Callable,
    stt_client: object | None,
    tts_client: object | None,
) -> object:
    """Start one already loaded bot config with a ready app-state wrapper."""
    from backend.app_state import AppState, AppStateManager

    app_state = AppStateManager(AppState.READY)
    return await _launch_configured_bot(
        service_type=service_type,
        config=config,
        app_state=app_state,
        orchestrator=orchestrator,
        llm_call=llm_call,
        stt_client=stt_client,
        tts_client=tts_client,
    )


async def _stop_runtime_bots(
    *,
    telegram_bot: object | None,
    discord_bot: object | None,
) -> None:
    """Stop the configured messenger bots during runtime shutdown."""
    if telegram_bot is not None:
        await telegram_bot.stop_polling()
    if discord_bot is not None:
        await discord_bot.stop()


async def _start_runtime_messenger_bots(
    *,
    startup: object,
) -> tuple[object | None, object | None]:
    """Start the configured messenger bots for one started runtime."""
    return await _start_configured_bots(
        db_client=startup.db_client,
        orchestrator=startup.services.orch,
        llm_call=startup.services.llm_call,
        stt_client=startup.services.stt_client,
        tts_client=startup.services.tts_client,
    )
