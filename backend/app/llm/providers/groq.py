"""Groq provider over its OpenAI-compatible `chat/completions` endpoint (httpx, no SDK).

As with Gemini, the answer is always validated against the Pydantic model here: the JSON
schema sent with the request shapes the answer but is not trusted. Rate limits (429), server
errors and network failures are retried; a 429 waits for the `retry-after` the API sends. The
API key travels in the Authorization header, never in a URL, a log or an error message.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from pydantic import BaseModel, ValidationError

from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

_RETRY_STATUS: Final = frozenset({429, 500, 502, 503, 504})
_ERROR_BODY_CHARS: Final = 300
_REASONING_EFFORTS: Final = frozenset({"low", "medium", "high"})
_DROPPED_KEYS: Final = frozenset({"$defs", "title"})


def groq_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of a Pydantic model with `$ref`s inlined and titles dropped.

    Args:
        schema: A Pydantic model.

    Returns:
        The `response_format.json_schema.schema` value.
    """
    raw = schema.model_json_schema()
    inlined = _inline(raw, raw.get("$defs", {}))
    if not isinstance(inlined, dict):
        raise ExternalServiceError("Model schema is not a JSON object")
    return inlined


def _inline(node: object, defs: Mapping[str, Any]) -> object:
    """Replace every `$ref` by its definition, recursively."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items() if k not in _DROPPED_KEYS}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def response_text(body: Mapping[str, Any]) -> str:
    """The answer text of a `chat/completions` response.

    Args:
        body: The decoded response.

    Returns:
        The message content of the first choice.

    Raises:
        ExternalServiceError: If there is no choice or no content.
    """
    choices = body.get("choices") or []
    if not choices:
        raise ExternalServiceError("Groq returned no choices")
    text = (choices[0].get("message") or {}).get("content") or ""
    if not text:
        raise ExternalServiceError(
            "Groq returned no text", details={"finish_reason": choices[0].get("finish_reason")}
        )
    return str(text)


def token_usage(body: Mapping[str, Any]) -> dict[str, int]:
    """Billed token counts of a response (0 where the API omits one).

    Args:
        body: The decoded response.

    Returns:
        Input and output tokens; output includes the model's reasoning.
    """
    usage = body.get("usage") or {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
    }


@dataclass(slots=True)
class GroqClient:
    """`LLMClient` over the Groq REST API; the HTTP client is injected."""

    http: httpx.Client
    model: str
    api_key: str = field(repr=False)
    api_base: str
    temperature: float
    max_output_tokens: int
    max_retries: int
    retry_backoff_seconds: float
    max_retry_after_seconds: float
    reasoning_effort: str | None = None
    sleep: Callable[[float], None] = time.sleep

    @property
    def model_name(self) -> str:
        """Identifier of the model answering."""
        return self.model

    def generate_json[T: BaseModel](self, prompt: str, schema: type[T]) -> T:
        """Answer a prompt with JSON validated against `schema`.

        Args:
            prompt: The full prompt.
            schema: The Pydantic model the answer must satisfy.

        Returns:
            The validated answer.

        Raises:
            ExternalServiceError: If the call fails after retries or the answer does not
                satisfy the schema.
        """
        body = self._post(self._payload(prompt, schema))
        logger.info("groq usage", extra=token_usage(body))
        text = response_text(body)
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise ExternalServiceError(
                "Groq answer does not satisfy the schema",
                details={"schema": schema.__name__, "errors": exc.error_count()},
            ) from exc

    def _payload(self, prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
        """The request body: one user message and a JSON-schema response format."""
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": groq_schema(schema)},
            },
        }
        if self.reasoning_effort in _REASONING_EFFORTS:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _post(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """POST with retries on transient failures; returns the decoded body."""
        url = f"{self.api_base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        failure = "no attempt made"
        wait = 0.0
        for attempt in range(self.max_retries + 1):
            if attempt:
                self.sleep(wait)
            wait = self.retry_backoff_seconds * 2**attempt
            try:
                response = self.http.post(url, json=payload, headers=headers)
            except httpx.TransportError as exc:
                failure = type(exc).__name__
                logger.warning("groq call failed", extra={"error": failure, "attempt": attempt})
                continue
            if response.status_code in _RETRY_STATUS:
                failure = f"HTTP {response.status_code}"
                wait = max(wait, self._retry_after(response))
                logger.warning("groq call failed", extra={"error": failure, "attempt": attempt})
                continue
            return _decoded(response)
        raise ExternalServiceError(
            "Groq call failed after retries",
            details={"error": failure, "attempts": self.max_retries + 1},
        )

    def _retry_after(self, response: httpx.Response) -> float:
        """Seconds the API asks to wait (capped), or 0 if it gives none."""
        try:
            seconds = float(response.headers.get("retry-after", "0"))
        except ValueError:
            return 0.0
        return min(max(seconds, 0.0), self.max_retry_after_seconds)


def _decoded(response: httpx.Response) -> dict[str, Any]:
    """The JSON body of a final response; a non-success status is an error."""
    if not response.is_success:
        raise ExternalServiceError(
            f"Groq returned HTTP {response.status_code}",
            details={"body": response.text[:_ERROR_BODY_CHARS]},
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ExternalServiceError("Groq returned a body that is not JSON") from exc
    if not isinstance(body, dict):
        raise ExternalServiceError("Groq returned a body that is not a JSON object")
    return body
