"""The LLM interface: one prompt in, one validated Pydantic model out (the provider is config)."""

from typing import Protocol

from pydantic import BaseModel


class LLMClient(Protocol):
    """Structured-output LLM. Implementations raise `ExternalServiceError` on any failure."""

    @property
    def model_name(self) -> str:
        """Identifier of the model answering."""
        ...

    def generate_json[T: BaseModel](self, prompt: str, schema: type[T]) -> T:
        """Answer a prompt with JSON validated against `schema`.

        Args:
            prompt: The full prompt.
            schema: The Pydantic model the answer must satisfy.

        Returns:
            The validated answer.
        """
        ...
