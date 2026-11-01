"""API request and response of the runs resource."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import RunStatus


class StartRunRequest(BaseModel):
    """Match a stored tender against these manufacturers."""

    model_config = ConfigDict(frozen=True)

    tender_id: uuid.UUID
    category: str = Field(min_length=1)
    manufacturers: list[str] = Field(min_length=1)


class RunResponse(BaseModel):
    """A run and how far it got."""

    model_config = ConfigDict(frozen=True)

    run_id: uuid.UUID
    tender_id: uuid.UUID
    category: str
    manufacturers: list[str]
    status: RunStatus
    error: str | None = Field(default=None, description="Why the run failed, if it did.")
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
