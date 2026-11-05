"""API response of the tenders resource."""

import uuid

from pydantic import BaseModel, ConfigDict


class TenderUploadResponse(BaseModel):
    """A stored bid.pdf, ready to be run."""

    model_config = ConfigDict(frozen=True)

    tender_id: uuid.UUID
    filename: str
    pages: int
    bid_number: str
    revision: int
    created: bool
