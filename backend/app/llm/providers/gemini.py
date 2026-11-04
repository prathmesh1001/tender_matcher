"""Gemini provider over the REST `generateContent` endpoint (httpx, no SDK).

The answer is always validated here against the Pydantic model: the schema sent to the API
helps the model answer in shape but is not trusted. Transient failures (rate limit, server
errors, network) are retried with exponential backoff; anything else fails at once. The API
key travels in a header, so it never appears in a URL, a log or an error message.
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


def gemini_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The API's schema for a Pydantic model (references inlined, `X | None` as nullable).

    Args:
        schema: A Pydantic model.

    Returns:
        The `responseSchema` value.
    """
    raw = schema.model_json_schema()
    return _convert(raw, raw.get("$defs", {}))


def _convert(node: Mapping[str, Any], defs: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one JSON-schema node to the API's schema subset."""
    if "$ref" in node:
        return _convert(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        if len(options) != 1:
            raise ExternalServiceError("Only `X | None` unions fit the Gemini schema")
        return {**_convert(options[0], defs), "nullable": True}
    out: dict[str, Any] = {"type": node["type"].upper()}
    if "enum" in node:
        out["enum"] = list(node["enum"])
    if "items" in node:
        out["items"] = _convert(node["items"], defs)
    if "properties" in node:
        out["properties"] = {k: _convert(v, defs) for k, v in node["properties"].items()}
        out["required"] = list(node.get("required", []))
    return out


def response_text(body: Mapping[str, Any]) -> str:
    """The generated text of a `generateContent` response.

    Args:
        body: The decoded response.

    Returns:
        The text parts of the first candidate, joined.

    Raises:
        ExternalServiceError: If the prompt was blocked or there is no text.
    """
    blocked = body.get("promptFeedback", {}).get("blockReason")
    if blocked:
        raise ExternalServiceError("Gemini blocked the prompt", details={"reason": blocked})
    candidates = body.get("candidates") or []
    if not candidates:
        raise ExternalServiceError("Gemini returned no candidates")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise ExternalServiceError(
            "Gemini returned no text", details={"finish_reason": candidates[0].get("finishReason")}
        )
    return text


def token_usage(body: Mapping[str, Any]) -> dict[str, int]:
    """Billed token counts of a `generateContent` response (0 where the API omits one).

    Args:
        body: The decoded response.

    Returns:
        Input, answer and thinking tokens; thinking tokens are billed as output.
    """
    usage = body.get("usageMetadata") or {}
    return {
        "input_tokens": int(usage.get("promptTokenCount") or 0),
        "output_tokens": int(usage.get("candidatesTokenCount") or 0),
        "thinking_tokens": int(usage.get("thoughtsTokenCount") or 0),
    }


@dataclass(slots=True)
class GeminiClient:
    """`LLMClient` over the Gemini REST API; the HTTP client is injected."""

    http: httpx.Client
    model: str
    api_key: str = field(repr=False)
    api_base: str
    temperature: float
    max_output_tokens: int
    max_retries: int
    retry_backoff_seconds: float
    thinking_level: str | None = None
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
            ExternalServiceError: If the call fails after retries, the prompt is blocked, or
                the answer does not satisfy the schema.
        """
        config: dict[str, Any] = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_output_tokens,
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema(schema),
        }
        if self.thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }
        body = self._post(payload)
        logger.info("gemini usage", extra=token_usage(body))
        text = response_text(body)
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise ExternalServiceError(
                "Gemini answer does not satisfy the schema",
                details={"schema": schema.__name__, "errors": exc.error_count()},
            ) from exc

    def _post(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """POST with retries on transient failures; returns the decoded body."""
        url = f"{self.api_base.rstrip('/')}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        failure = "no attempt made"
        for attempt in range(self.max_retries + 1):
            if attempt:
                self.sleep(self.retry_backoff_seconds * 2 ** (attempt - 1))
            try:
                response = self.http.post(url, json=payload, headers=headers)
            except httpx.TransportError as exc:
                failure = type(exc).__name__
                logger.warning("gemini call failed", extra={"error": failure, "attempt": attempt})
                continue
            if response.status_code in _RETRY_STATUS:
                failure = f"HTTP {response.status_code}"
                logger.warning("gemini call failed", extra={"error": failure, "attempt": attempt})
                continue
            return _decoded(response)
        raise ExternalServiceError(
            "Gemini call failed after retries",
            details={"error": failure, "attempts": self.max_retries + 1},
        )


def _decoded(response: httpx.Response) -> dict[str, Any]:
    """The JSON body of a final response; a non-success status is an error."""
    if not response.is_success:
        raise ExternalServiceError(
            f"Gemini returned HTTP {response.status_code}",
            details={"body": response.text[:_ERROR_BODY_CHARS]},
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ExternalServiceError("Gemini returned a body that is not JSON") from exc
    if not isinstance(body, dict):
        raise ExternalServiceError("Gemini returned a body that is not a JSON object")
    return body
