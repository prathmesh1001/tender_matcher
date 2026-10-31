"""Gemini provider against a fake HTTP transport: no network, every response is synthetic."""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError, InvalidInputError
from app.llm.factory import build_llm_client
from app.llm.providers.gemini import GeminiClient, gemini_schema

KEY = "secret-test-key"


class _Inner(BaseModel):
    name: str
    note: str | None = None


class _Answer(BaseModel):
    items: list[_Inner]


def _ok(text: str = '{"items": [{"name": "a"}]}') -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def _client(handler: Callable[[httpx.Request], httpx.Response], retries: int = 2) -> GeminiClient:
    return GeminiClient(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        model="test-model",
        api_key=KEY,
        api_base="https://llm.invalid/v1beta/",
        temperature=0.0,
        max_output_tokens=100,
        max_retries=retries,
        retry_backoff_seconds=1.0,
        sleep=lambda _: None,
    )


def test_request_shape_and_validated_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    answer = _client(handler).generate_json("the prompt", _Answer)
    assert answer == _Answer(items=[_Inner(name="a")])
    [request] = seen
    assert str(request.url) == "https://llm.invalid/v1beta/models/test-model:generateContent"
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["contents"][0]["parts"][0]["text"] == "the prompt"
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert (config["temperature"], config["maxOutputTokens"]) == (0.0, 100)


def test_schema_inlines_references_and_marks_nullable() -> None:
    schema = gemini_schema(_Answer)
    assert schema["type"] == "OBJECT"
    item = schema["properties"]["items"]["items"]
    assert item["properties"]["name"] == {"type": "STRING"}
    assert item["properties"]["note"] == {"type": "STRING", "nullable": True}
    assert item["required"] == ["name"]
    assert "$ref" not in json.dumps(schema)


def test_thought_parts_are_not_the_answer() -> None:
    parts = [{"text": "thinking", "thought": True}, {"text": '{"items": []}'}]
    body = {"candidates": [{"content": {"parts": parts}}]}
    answer = _client(lambda _: httpx.Response(200, json=body)).generate_json("p", _Answer)
    assert answer.items == []


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_status_is_retried_with_growing_delay(status: int) -> None:
    replies = [httpx.Response(status), httpx.Response(status), _ok()]
    delays: list[float] = []
    client = _client(lambda _: replies.pop(0))
    client.sleep = delays.append
    assert client.generate_json("p", _Answer).items[0].name == "a"
    assert delays == [1.0, 2.0]


def test_network_failure_is_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("down", request=request)
        return _ok()

    assert _client(handler).generate_json("p", _Answer).items[0].name == "a"


def test_gives_up_after_retries() -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(ExternalServiceError) as caught:
        _client(handler, retries=2).generate_json("p", _Answer)
    assert len(calls) == 3
    assert caught.value.details == {"error": "HTTP 503", "attempts": 3}


def test_client_error_is_not_retried_and_never_shows_the_key() -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad request")

    with pytest.raises(ExternalServiceError) as caught:
        _client(handler).generate_json("p", _Answer)
    assert len(calls) == 1
    assert KEY not in str(caught.value)
    assert KEY not in str(caught.value.details)
    assert KEY not in repr(_client(handler))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "blocked"),
        (httpx.Response(200, json={"candidates": []}), "no candidates"),
        (
            httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS"}]}),
            "no text",
        ),
        (httpx.Response(200, text="<html>"), "not JSON"),
        (httpx.Response(200, json=[1]), "not a JSON object"),
        (_ok("not json"), "does not satisfy"),  # e.g. cut off by the token limit
        (_ok('{"items": [{"wrong": 1}]}'), "does not satisfy"),
    ],
)
def test_bad_answers_raise(response: httpx.Response, message: str) -> None:
    with pytest.raises(ExternalServiceError, match=message):
        _client(lambda _: response).generate_json("p", _Answer)


def test_factory_needs_the_key(settings: Settings) -> None:
    with pytest.raises(InvalidInputError, match="GEMINI_API_KEY"):
        build_llm_client(settings.model_copy(update={"GEMINI_API_KEY": None}))


def test_factory_uses_the_configured_model(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"GEMINI_API_KEY": SecretStr(KEY), "LLM_MODEL": "other-model"}
    )
    assert build_llm_client(configured).model_name == "other-model"
