"""Groq provider against a fake HTTP transport: no network, every response is synthetic."""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.core.config import LLMProvider, Settings
from app.core.exceptions import ExternalServiceError, InvalidInputError
from app.llm.factory import build_llm_client, llm_configured
from app.llm.providers.groq import GroqClient, groq_schema, token_usage

KEY = "secret-groq-key"


class _Inner(BaseModel):
    name: str
    note: str | None = None


class _Answer(BaseModel):
    items: list[_Inner]


def _ok(text: str = '{"items": [{"name": "a"}]}') -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    retries: int = 2,
    reasoning_effort: str | None = None,
    sleeps: list[float] | None = None,
) -> GroqClient:
    waits = sleeps if sleeps is not None else []
    return GroqClient(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        model="openai/gpt-oss-120b",
        api_key=KEY,
        api_base="https://llm.invalid/openai/v1/",
        temperature=0.0,
        max_output_tokens=512,
        max_retries=retries,
        retry_backoff_seconds=1.0,
        max_retry_after_seconds=30.0,
        reasoning_effort=reasoning_effort,
        sleep=waits.append,
    )


def _groq_settings(settings: Settings, key: str | None = KEY) -> Settings:
    return settings.model_copy(
        update={
            "LLM_PROVIDER": LLMProvider.GROQ,
            "LLM_MODEL": "openai/gpt-oss-120b",
            "GROQ_API_KEY": None if key is None else SecretStr(key),
        }
    )


def test_request_shape_and_validated_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    answer = _client(handler, reasoning_effort="low").generate_json("the prompt", _Answer)

    assert answer == _Answer(items=[_Inner(name="a")])
    request = seen[0]
    assert str(request.url) == "https://llm.invalid/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"] == [{"role": "user", "content": "the prompt"}]
    assert body["max_completion_tokens"] == 512
    assert body["reasoning_effort"] == "low"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "_Answer"


@pytest.mark.parametrize("effort", [None, "", "minimal"])
def test_unknown_reasoning_effort_is_not_sent(effort: str | None) -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok()

    _client(handler, reasoning_effort=effort).generate_json("p", _Answer)
    assert "reasoning_effort" not in seen[0]


def test_schema_inlines_references_and_drops_titles() -> None:
    schema = groq_schema(_Answer)
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text
    assert '"title"' not in text
    assert schema["properties"]["items"]["items"]["properties"]["name"] == {"type": "string"}


def test_token_usage_reads_counts_and_defaults_to_zero() -> None:
    body = {"usage": {"prompt_tokens": 10, "completion_tokens": 4}}
    assert token_usage(body) == {"input_tokens": 10, "output_tokens": 4}
    assert token_usage({}) == {"input_tokens": 0, "output_tokens": 0}


def test_rate_limit_waits_for_retry_after_capped() -> None:
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(429, headers={"retry-after": "500"}),
            _ok(),
        ]
    )
    sleeps: list[float] = []
    _client(lambda _: next(responses), sleeps=sleeps).generate_json("p", _Answer)
    assert sleeps == [7.0, 30.0]


@pytest.mark.parametrize("status", [500, 503])
def test_server_error_is_retried_with_growing_delay(status: int) -> None:
    responses = iter([httpx.Response(status), httpx.Response(status), _ok()])
    sleeps: list[float] = []
    _client(lambda _: next(responses), sleeps=sleeps).generate_json("p", _Answer)
    assert sleeps == [1.0, 2.0]


def test_network_failure_is_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down", request=request)
        return _ok()

    assert _client(handler).generate_json("p", _Answer).items[0].name == "a"


def test_gives_up_after_retries() -> None:
    with pytest.raises(ExternalServiceError, match="after retries"):
        _client(lambda _: httpx.Response(503), retries=1).generate_json("p", _Answer)


def test_client_error_is_not_retried_and_never_shows_the_key() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    with pytest.raises(ExternalServiceError) as info:
        _client(handler).generate_json("p", _Answer)
    assert calls["n"] == 1
    assert "HTTP 400" in str(info.value)
    assert KEY not in str(info.value)
    assert KEY not in repr(_client(handler))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, json={"choices": []}), "no choices"),
        (httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}), "no text"),
        (_ok('{"items": [{"nome": "a"}]}'), "does not satisfy"),
        (httpx.Response(200, text="not json"), "not JSON"),
        (httpx.Response(200, json=[1]), "not a JSON object"),
    ],
)
def test_bad_answers_raise(response: httpx.Response, message: str) -> None:
    with pytest.raises(ExternalServiceError, match=message):
        _client(lambda _: response).generate_json("p", _Answer)


def test_factory_builds_groq_with_model_and_effort(settings: Settings) -> None:
    client = build_llm_client(_groq_settings(settings), "low")
    assert isinstance(client, GroqClient)
    assert client.model_name == "openai/gpt-oss-120b"
    assert client.reasoning_effort == "low"


def test_factory_needs_the_groq_key(settings: Settings) -> None:
    assert not llm_configured(_groq_settings(settings, key=None))
    assert not llm_configured(_groq_settings(settings, key=" "))
    assert llm_configured(_groq_settings(settings))
    with pytest.raises(InvalidInputError, match="GROQ_API_KEY"):
        build_llm_client(_groq_settings(settings, key=None))


def test_groq_key_alone_does_not_configure_gemini(settings: Settings) -> None:
    gemini = settings.model_copy(update={"GEMINI_API_KEY": None, "GROQ_API_KEY": SecretStr(KEY)})
    assert not llm_configured(gemini)
