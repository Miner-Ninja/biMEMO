"""Application configuration schema using Pydantic Settings.

Defines all configuration variables loaded from environment. Every field
is required with no defaults — missing configuration causes an immediate
startup error. Covers database paths, key source, SearXNG URL, and server
binding. LLM provider credentials and Telegram credentials are stored
exclusively in the encrypted SQLCipher database — not in environment variables.
"""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


SUPPORTED_KEY_SOURCES = ("file", "password")
SUPPORTED_LLM_RESPONSE_FORMATS = ("text", "json_schema")


def _supported_key_sources_text() -> str:
    """Return the configured key source list for error messages."""
    return ", ".join(SUPPORTED_KEY_SOURCES)


def _supported_llm_response_formats_text() -> str:
    """Return the configured LLM response format list for error messages."""
    return ", ".join(SUPPORTED_LLM_RESPONSE_FORMATS)


class Settings(BaseSettings):
    """Application-wide configuration loaded from environment variables.

    LLM provider credentials (API keys, model names, base URLs) and
    Telegram credentials are NOT here — they live in the database and
    are loaded by the Orchestrator at startup via get_integration_config.

    Attributes:
        debug: Enable debug mode (additional logging, never in production).
        key_source: Key source type for SQLCipher encryption.
        key_file_path: Path to the 32-byte key file (required when key_source='file').
        encrypted_key_file_path: Path to the encrypted master-key file.
        automatic_unlock: Enable automatic unlock from a configured password source.
        unlock_password_file: Optional path to an automatic unlock password file.
        unlock_password: Optional development-only automatic unlock password.
        database_path: Path to the SQLCipher database file.
        diagnostics_database_path: Optional path to the encrypted diagnostics DB.
        diagnostics_retention_days: Number of days to keep route diagnostics.
        searxng_url: Base URL for the local SearXNG search instance.
        host: HTTP server bind address.
        port: HTTP server bind port.
        llm_response_format: LLM transport format (text or json_schema).
        llm_response_stream: Whether json_schema responses stream
            token-by-token (true) or block until the full JSON body
            arrives (false). Ignored when llm_response_format=text,
            which always streams.
    """

    model_config = {"env_prefix": "BIMEMO_"}

    debug: bool
    key_source: str = ""
    key_file_path: str = ""
    encrypted_key_file_path: str = ""
    automatic_unlock: bool = False
    unlock_password_file: str | None = None
    unlock_password: str | None = None
    database_path: str
    diagnostics_database_path: str = ""
    diagnostics_retention_days: int = 14
    searxng_url: str
    host: str
    port: int
    llm_response_format: str
    llm_response_stream: bool
    whisper_endpoint: str = ""
    whisper_language: str = ""
    whisper_model: str = ""
    tts_endpoint: str = ""
    tts_voice_id: str = ""
    discord_bot_token: str = ""
    discord_allowed_user_ids: str = ""
    discord_persona_user_ids: str = ""
    discord_bridge_category_id: str = Field(
        default="",
        validation_alias="DISCORD_BRIDGE_CATEGORY_ID",
    )
    discord_bridge_server_id: str = Field(
        default="",
        validation_alias="DISCORD_BRIDGE_SERVER_ID",
    )
    discord_bridge_token: str = Field(
        default="",
        validation_alias="DISCORD_BRIDGE_TOKEN",
    )

    @model_validator(mode="after")
    def _check_key_source_fields(self) -> Settings:
        """Validate key-source-specific fields are set."""
        if self.key_source not in SUPPORTED_KEY_SOURCES:
            msg = (
                f"Invalid key_source: '{self.key_source}'. "
                f"Must be one of: {_supported_key_sources_text()}"
            )
            raise ValueError(msg)
        if self.key_source == "file" and not self.key_file_path:
            msg = "key_file_path is required when key_source is 'file'"
            raise ValueError(msg)
        if self.key_source == "password" and not self.encrypted_key_file_path:
            msg = "encrypted_key_file_path is required when key_source is 'password'"
            raise ValueError(msg)
        if (
            self.key_source == "password"
            and self.automatic_unlock
            and not (self.unlock_password_file or self.unlock_password)
        ):
            msg = "automatic_unlock requires unlock_password_file or unlock_password"
            raise ValueError(msg)
        if self.diagnostics_retention_days < 1:
            msg = "diagnostics_retention_days must be at least 1"
            raise ValueError(msg)
        if self.llm_response_format not in SUPPORTED_LLM_RESPONSE_FORMATS:
            msg = (
                f"Invalid llm_response_format: '{self.llm_response_format}'. "
                f"Must be one of: {_supported_llm_response_formats_text()}"
            )
            raise ValueError(msg)
        return self


_settings: Settings | None = None


def parse_configured_id_list(raw_value: str) -> frozenset[str]:
    """Normalize one comma-separated identifier list from settings."""
    return frozenset(_configured_id_items(raw_value))


def _configured_id_items(raw_value: str) -> list[str]:
    """Return normalized non-empty identifiers from one raw settings string."""
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def get_settings() -> Settings:
    """Return the singleton application settings instance.

    Returns:
        The validated Settings object.

    Raises:
        ValidationError: If required environment variables are missing.
    """
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


def reset_settings() -> None:
    """Reset the singleton settings instance for testing."""
    global _settings
    _settings = None
