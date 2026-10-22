"""Error response schemas shared by every endpoint."""

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """Machine-readable description of a failed request."""

    code: str = Field(description="Stable error code, e.g. `not_found`.")
    message: str = Field(description="Human-readable explanation.")
    details: dict[str, object] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Envelope returned for every non-2xx response produced by the app."""

    error: ErrorBody
