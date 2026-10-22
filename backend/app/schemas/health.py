"""Health check schemas."""

from typing import Literal

from pydantic import BaseModel

from app.core.config import Environment


class HealthResponse(BaseModel):
    """Liveness information about the running backend."""

    status: Literal["ok"]
    app: str
    version: str
    environment: Environment
