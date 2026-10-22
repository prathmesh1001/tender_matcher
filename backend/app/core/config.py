"""Application configuration.

Every environment variable the backend reads is declared here and documented in `.env.example`.
No other module may read `os.environ` directly.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment the app is running in."""

    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Supported log levels (names match the stdlib `logging` levels)."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables and an optional `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    APP_NAME: str = Field(default="Tender Matcher", description="Name shown in OpenAPI and logs.")
    APP_VERSION: str = Field(default="0.1.0", description="Version reported by the health route.")
    ENVIRONMENT: Environment = Field(default=Environment.LOCAL)
    API_PREFIX: str = Field(default="/api/v1", pattern=r"^/[a-z0-9/_-]*[a-z0-9]$")
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO)
    LOG_JSON: bool = Field(
        default=True, description="Emit JSON logs; set false for readable dev logs."
    )
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        description="Allowed browser origins, as a JSON list.",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so the environment is read once. Tests should build `Settings(...)` directly and pass
    it to `create_app` instead of mutating the environment.

    Returns:
        The loaded settings.
    """
    return Settings()
