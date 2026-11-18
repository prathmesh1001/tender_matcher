"""Build the configured `LLMClient` (the one place that knows the providers)."""

import httpx
from pydantic import SecretStr

from app.core.config import LLMProvider, Settings
from app.core.exceptions import InvalidInputError
from app.llm.base import LLMClient
from app.llm.providers.gemini import GeminiClient
from app.llm.providers.groq import GroqClient


def _provider_key(settings: Settings) -> tuple[str, SecretStr | None]:
    """The setting name and value of the configured provider's API key."""
    match settings.LLM_PROVIDER:
        case LLMProvider.GEMINI:
            return "GEMINI_API_KEY", settings.GEMINI_API_KEY
        case LLMProvider.GROQ:
            return "GROQ_API_KEY", settings.GROQ_API_KEY


def llm_configured(settings: Settings) -> bool:
    """True if the configured provider has its API key (an empty value counts as missing).

    Args:
        settings: Application settings.

    Returns:
        Whether `build_llm_client` can build a client.
    """
    _, key = _provider_key(settings)
    return key is not None and bool(key.get_secret_value().strip())


def build_llm_client(settings: Settings, thinking_level: str | None = None) -> LLMClient:
    """Build the LLM client from settings.

    Args:
        settings: Application settings.
        thinking_level: Provider thinking level for this caller (Gemini thinkingLevel, Groq
            reasoning_effort); None keeps the model default.

    Returns:
        A ready client.

    Raises:
        InvalidInputError: If the provider's API key is not configured.
    """
    name, key = _provider_key(settings)
    if key is None or not llm_configured(settings):
        raise InvalidInputError(f"{name} is not set (see .env.example)")
    http = httpx.Client(timeout=settings.LLM_TIMEOUT_SECONDS)
    match settings.LLM_PROVIDER:
        case LLMProvider.GEMINI:
            return GeminiClient(
                http=http,
                model=settings.LLM_MODEL,
                api_key=key.get_secret_value(),
                api_base=settings.GEMINI_API_BASE,
                temperature=settings.LLM_TEMPERATURE,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                max_retries=settings.LLM_MAX_RETRIES,
                retry_backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                thinking_level=thinking_level,
            )
        case LLMProvider.GROQ:
            return GroqClient(
                http=http,
                model=settings.LLM_MODEL,
                api_key=key.get_secret_value(),
                api_base=settings.GROQ_API_BASE,
                temperature=settings.LLM_TEMPERATURE,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                max_retries=settings.LLM_MAX_RETRIES,
                retry_backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                max_retry_after_seconds=settings.LLM_MAX_RETRY_AFTER_SECONDS,
                reasoning_effort=thinking_level,
            )
