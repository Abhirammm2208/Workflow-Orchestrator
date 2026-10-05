"""
app/config.py
Application configuration loaded from environment variables via pydantic-settings.
All settings have sensible defaults for local development.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # LLM — Nvidia Nemotron via OpenAI-compatible NIM endpoint
    # -------------------------------------------------------------------------
    nvidia_api_key: str = "nvapi-placeholder"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    llm_timeout_seconds: int = 30
    use_mock_llm: bool = False  # Set True to skip real API calls in tests

    # -------------------------------------------------------------------------
    # PostgreSQL — three URL formats for three consumers
    # -------------------------------------------------------------------------
    # SQLAlchemy ORM (asyncpg driver — fastest async ORM queries)
    # NOTE: @ in password is URL-encoded as %40; use 127.0.0.1 not localhost
    database_url: str = (
        "postgresql+asyncpg://postgres:Abhiram%40123@127.0.0.1:5432/Workflow"
    )

    # LangGraph AsyncPostgresSaver (psycopg3 driver — required by checkpointer)
    psycopg_url: str = (
        "postgresql+psycopg://postgres:Abhiram%40123@127.0.0.1:5432/Workflow"
    )

    # Raw DSN passed directly to psycopg.AsyncConnection.connect()
    checkpoint_db_uri: str = (
        "postgresql://postgres:Abhiram%40123@127.0.0.1:5432/Workflow"
    )

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    secret_key: str = "change-me-to-a-random-32-char-secret"

    # -------------------------------------------------------------------------
    # Workflow behaviour
    # -------------------------------------------------------------------------
    max_retry_attempts: int = 3

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()


# Module-level convenience alias — import this everywhere
settings = get_settings()
