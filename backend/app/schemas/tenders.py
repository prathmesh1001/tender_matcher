"""API response of the tenders resource."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class TenderUploadResponse(BaseModel):
    """A stored bid.pdf, ready to be run."""

    model_config = ConfigDict(frozen=True)

    tender_id: uuid.UUID
    filename: str
    pages: int
    bid_number: str
    revision: int
    created: bool
    attachments: list[str] = Field(
        default_factory=list, description="Files stored from an uploaded ZIP besides bid.pdf."
    )
    skipped: list[str] = Field(
        default_factory=list, description="Files in an uploaded ZIP that were not stored, and why."
    )
