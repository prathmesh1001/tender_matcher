"""Application configuration.

Every environment variable the backend reads is declared here and documented in `.env.example`.
No other module may read `os.environ` directly.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, field_validator, model_validator
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


class FileStoreBackend(StrEnum):
    """Where source files are stored (Postgres is added in plan step 16)."""

    LOCAL = "local"


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

    DATABASE_URL: PostgresDsn = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5433/db. Required."
    )
    DB_ECHO: bool = Field(default=False, description="Log every SQL statement (debug only).")
    DB_POOL_SIZE: int = Field(default=5, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0)
    DB_POOL_TIMEOUT_S: float = Field(default=30.0, gt=0)

    FILE_STORE_BACKEND: FileStoreBackend = Field(default=FileStoreBackend.LOCAL)
    FILE_STORE_ROOT: Path = Field(
        default=Path("../data"),
        description="Root of the local file store; documents store paths relative to it.",
    )
    MANUFACTURER_DATA_DIR: Path = Field(
        default=Path("../data/manufacturers"),
        description="Root holding one folder per manufacturer, each with PDFs + sources.yaml.",
    )
    TENDER_DATA_DIR: Path = Field(
        default=Path("../data/tenders"),
        description="Root holding one folder per tender, each with bid.pdf + tender.yaml.",
    )
    ONTOLOGY_DIR: Path = Field(
        default=Path("ontology"), description="Folder with one ontology YAML per category."
    )

    TABLE_CONTINUATION_EDGE_FRACTION: float = Field(
        default=0.25,
        gt=0,
        lt=0.5,
        description="A table ending/starting this close to a page edge may continue across it.",
    )

    TENDER_LINK_RULES_FILE: Path = Field(
        default=Path("config/tender_links.yaml"),
        description="URL patterns deciding which links in a tender are fetched or skipped.",
    )
    TENDER_LINK_MAX_DEPTH: int = Field(
        default=3, ge=1, le=5, description="Follow links this many hops from bid.pdf."
    )
    HTTP_TIMEOUT_S: float = Field(default=60.0, gt=0, description="Per-request download timeout.")
    HTTP_MAX_DOWNLOAD_MB: int = Field(
        default=100, ge=1, description="Larger linked files are refused."
    )
    HTTP_USER_AGENT: str = Field(
        default="tender-matcher/0.1 (+document fetcher)",
        min_length=1,
        description="User-Agent sent when downloading tender attachments.",
    )

    CHUNK_TARGET_TOKENS: int = Field(
        default=400, ge=50, description="Aim for prose chunks of this size."
    )
    CHUNK_MAX_TOKENS: int = Field(default=500, ge=50, description="Hard cap; longer text is split.")
    CHUNK_OVERLAP_TOKENS: int = Field(
        default=50, ge=0, description="Repeated between prose chunks."
    )
    CHUNK_HEADING_MAX_WORDS: int = Field(
        default=10, ge=1, description="Text blocks longer than this are never treated as headings."
    )
    CHUNK_MODEL_HEADER_KEYWORDS: list[str] = Field(
        default_factory=lambda: ["model", "type", "size"],
        min_length=1,
        description="Table header words that mark the model column, as a JSON list.",
    )

    OCR_ENABLED: bool = Field(default=True, description="OCR pages that have no text layer.")
    OCR_MIN_TEXT_CHARS: int = Field(
        default=50,
        ge=0,
        description="Below this many non-whitespace text-layer characters a page is 'sparse'.",
    )
    OCR_MIN_IMAGE_COVERAGE: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="A sparse page is OCR'd only if images cover at least this page fraction.",
    )
    OCR_DPI: int = Field(default=300, ge=72, le=600, description="Render resolution for OCR.")
    OCR_LANGUAGE: str = Field(
        default="eng+hin",
        pattern=r"^[a-z_]+(\+[a-z_]+)*$",
        description="Tesseract language code(s); GeM tenders are often bilingual Hindi/English.",
    )
    TESSERACT_CMD: str | None = Field(
        default=None, description="Path to the tesseract binary if it is not on PATH."
    )
    TESSDATA_DIR: Path | None = Field(
        default=None,
        description="Folder with *.traineddata files, if not Tesseract's built-in tessdata.",
    )

    @model_validator(mode="after")
    def _chunk_sizes_are_consistent(self) -> Settings:
        """Target must fit under the cap, and overlap must be smaller than the target."""
        if self.CHUNK_TARGET_TOKENS > self.CHUNK_MAX_TOKENS:
            raise ValueError("CHUNK_TARGET_TOKENS must be <= CHUNK_MAX_TOKENS")
        if self.CHUNK_OVERLAP_TOKENS >= self.CHUNK_TARGET_TOKENS:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be < CHUNK_TARGET_TOKENS")
        return self

    @field_validator("OCR_LANGUAGE")
    @classmethod
    def _english_first(cls, value: str) -> str:
        """Require `eng` as the primary language.

        Tesseract favours the first language: with `hin` first, Latin spec codes are misread
        (measured: `SS316` -> `55376`, `1450 RPM` -> `450 RPM`), which would corrupt evidence.
        """
        if value.split("+")[0] != "eng":
            raise ValueError("OCR_LANGUAGE must start with 'eng', e.g. 'eng' or 'eng+hin'")
        return value

    @field_validator("TESSDATA_DIR")
    @classmethod
    def _tessdata_dir_without_spaces(cls, value: Path | None) -> Path | None:
        """Reject whitespace: pytesseract cannot pass a quoted path to Tesseract on Windows."""
        if value is not None and any(ch.isspace() for ch in str(value)):
            raise ValueError("TESSDATA_DIR must not contain spaces; use a path like C:/tessdata")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so the environment is read once. Tests should build `Settings(...)` directly and pass
    it to `create_app` instead of mutating the environment.

    Returns:
        The loaded settings.
    """
    return Settings()
