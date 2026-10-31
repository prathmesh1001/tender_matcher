"""Build the configured `LLMClient` (the one place that knows the providers)."""

import httpx

from app.core.config import LLMProvider, Settings
from app.core.exceptions import InvalidInputError
from app.llm.base import LLMClient
from app.llm.providers.gemini import GeminiClient


def build_llm_client(settings: Settings) -> LLMClient:
    """Build the LLM client from settings.

    Args:
        settings: Application settings.

    Returns:
        A ready client.

    Raises:
        InvalidInputError: If the provider's API key is not configured.
    """
    match settings.LLM_PROVIDER:
        case LLMProvider.GEMINI:
            if settings.GEMINI_API_KEY is None:
                raise InvalidInputError("GEMINI_API_KEY is not set (see .env.example)")
            return GeminiClient(
                http=httpx.Client(timeout=settings.LLM_TIMEOUT_SECONDS),
                model=settings.LLM_MODEL,
                api_key=settings.GEMINI_API_KEY.get_secret_value(),
                api_base=settings.GEMINI_API_BASE,
                temperature=settings.LLM_TEMPERATURE,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                max_retries=settings.LLM_MAX_RETRIES,
                retry_backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
            )
