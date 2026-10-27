import pytest
from pydantic import ValidationError

from app.core.config import Environment, LogLevel, Settings

DB_URL = "postgresql+psycopg://u:p@localhost:5433/db"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"DATABASE_URL": DB_URL, **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_defaults_are_sane(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("APP_NAME", "API_PREFIX", "LOG_LEVEL", "ENVIRONMENT", "CORS_ORIGINS", "DB_ECHO"):
        monkeypatch.delenv(var, raising=False)
    settings = _settings()
    assert settings.API_PREFIX == "/api/v1"
    assert settings.LOG_LEVEL is LogLevel.INFO
    assert settings.ENVIRONMENT is Environment.LOCAL
    assert settings.DB_ECHO is False


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.example", "http://b.example"]')
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@db:5432/prod")
    settings = Settings(_env_file=None)
    assert settings.ENVIRONMENT is Environment.PRODUCTION
    assert settings.LOG_LEVEL is LogLevel.DEBUG
    assert settings.CORS_ORIGINS == ["http://a.example", "http://b.example"]
    assert settings.DATABASE_URL.hosts()[0]["host"] == "db"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None)


@pytest.mark.parametrize("url", ["sqlite:///x.db", "not a url", "mysql://u:p@h/db"])
def test_database_url_must_be_postgres(url: str) -> None:
    with pytest.raises(ValidationError):
        _settings(DATABASE_URL=url)


@pytest.mark.parametrize("prefix", ["api/v1", "/api/v1/", "/API"])
def test_rejects_malformed_api_prefix(prefix: str) -> None:
    with pytest.raises(ValidationError):
        _settings(API_PREFIX=prefix)


def test_rejects_unknown_log_level() -> None:
    with pytest.raises(ValidationError):
        _settings(LOG_LEVEL="LOUD")


@pytest.mark.parametrize("field", ["DB_POOL_SIZE", "DB_POOL_TIMEOUT_S"])
def test_rejects_non_positive_pool_settings(field: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: 0})


def test_settings_are_immutable() -> None:
    settings = _settings()
    with pytest.raises(ValidationError):
        settings.APP_NAME = "changed"  # type: ignore[misc]


def test_ocr_language_defaults_to_english_plus_hindi() -> None:
    assert _settings().OCR_LANGUAGE == "eng+hin"


@pytest.mark.parametrize("language", ["eng", "eng+hin", "eng+hin+mar"])
def test_accepts_english_first_ocr_languages(language: str) -> None:
    assert language == _settings(OCR_LANGUAGE=language).OCR_LANGUAGE


@pytest.mark.parametrize("language", ["hin+eng", "hin", "eng hin", "ENG", ""])
def test_rejects_ocr_language_not_starting_with_eng(language: str) -> None:
    with pytest.raises(ValidationError):
        _settings(OCR_LANGUAGE=language)


def test_rejects_tessdata_dir_with_spaces() -> None:
    with pytest.raises(ValidationError, match="spaces"):
        _settings(TESSDATA_DIR="C:/Program Files/Tesseract-OCR/tessdata")
    assert _settings(TESSDATA_DIR="C:/tessdata").TESSDATA_DIR is not None


def test_chunk_target_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError, match="CHUNK_TARGET_TOKENS"):
        _settings(CHUNK_TARGET_TOKENS=600, CHUNK_MAX_TOKENS=500)


def test_chunk_overlap_must_be_smaller_than_target() -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS"):
        _settings(CHUNK_TARGET_TOKENS=100, CHUNK_OVERLAP_TOKENS=100)
