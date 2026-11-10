"""API runs use the judge by default, and fall back to rules only without an API key."""

import logging

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.services import run_service
from app.services.run_service import judge_or_none


def test_the_judge_is_on_by_default() -> None:
    assert Settings.model_fields["LLM_JUDGE_ENABLED"].default is True


def test_disabled_judge_gives_rules_only(settings: Settings) -> None:
    assert judge_or_none(settings.model_copy(update={"LLM_JUDGE_ENABLED": False}), None) is None  # type: ignore[arg-type]


def test_enabled_judge_without_a_key_gives_rules_only_with_a_warning(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    keyless = settings.model_copy(update={"LLM_JUDGE_ENABLED": True, "GEMINI_API_KEY": None})
    with caplog.at_level(logging.WARNING):
        assert judge_or_none(keyless, None) is None  # type: ignore[arg-type]
    assert "rules only" in caplog.text


def test_enabled_judge_with_a_key_is_built(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = object()
    monkeypatch.setattr(run_service, "build_judge_service", lambda *_: built)
    keyed = settings.model_copy(
        update={"LLM_JUDGE_ENABLED": True, "GEMINI_API_KEY": SecretStr("k")}
    )
    assert judge_or_none(keyed, None) is built  # type: ignore[arg-type]
