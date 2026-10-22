import pytest
from pydantic import ValidationError

from app.core.config import Environment, LogLevel, Settings


def test_defaults_are_sane(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("APP_NAME", "API_PREFIX", "LOG_LEVEL", "ENVIRONMENT", "CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.API_PREFIX == "/api/v1"
    assert settings.LOG_LEVEL is LogLevel.INFO
    assert settings.ENVIRONMENT is Environment.LOCAL


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.example", "http://b.example"]')
    settings = Settings(_env_file=None)
    assert settings.ENVIRONMENT is Environment.PRODUCTION
    assert settings.LOG_LEVEL is LogLevel.DEBUG
    assert settings.CORS_ORIGINS == ["http://a.example", "http://b.example"]


@pytest.mark.parametrize("prefix", ["api/v1", "/api/v1/", "/API"])
def test_rejects_malformed_api_prefix(prefix: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, API_PREFIX=prefix)


def test_rejects_unknown_log_level() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LOG_LEVEL="LOUD")  # type: ignore[arg-type]


def test_settings_are_immutable() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        settings.APP_NAME = "changed"  # type: ignore[misc]
