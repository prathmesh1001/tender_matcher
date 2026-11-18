"""API request and response of the discovery resource (GeM Marketplace listings)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import DiscoveryStatus


class ListingResult(BaseModel):
    """One GeM listing read for a product type, and whether it was kept."""

    model_config = ConfigDict(frozen=True)

    url: str
    kept: bool
    reason: str
    title: str = ""
    brand: str | None = None
    model: str | None = None
    snapshot_ref: str | None = Field(
        default=None, description="File store reference of the frozen HTML, if it was saved."
    )


class DiscoveryJobResponse(BaseModel):
    """One GeM Marketplace check and how far it got."""

    model_config = ConfigDict(frozen=True)

    job_id: uuid.UUID
    run_id: uuid.UUID
    product_type: str
    category: str
    status: DiscoveryStatus
    error: str | None = None
    listings: list[ListingResult] = Field(default_factory=list)
    result_run_id: uuid.UUID | None = Field(
        default=None, description="The re-matched run with the kept listings, once done."
    )
    created_at: datetime
    finished_at: datetime | None = None


class UncoveredTypeResponse(BaseModel):
    """A product type of the run's items that no stored document covers."""

    model_config = ConfigDict(frozen=True)

    product_type: str
    category: str
    item_nos: list[str]
    gem_configured: bool = Field(description="config/gem_marketplace.yaml lists categories.")
    jobs: list[DiscoveryJobResponse] = Field(
        default_factory=list, description="Checks already asked for this type, newest first."
    )


class DiscoveryOverview(BaseModel):
    """What the run page shows: whether discovery is on, and the uncovered types."""

    model_config = ConfigDict(frozen=True)

    enabled: bool
    uncovered: list[UncoveredTypeResponse] = Field(default_factory=list)


class StartDiscoveryRequest(BaseModel):
    """Check GeM Marketplace for one uncovered product type of a run."""

    model_config = ConfigDict(frozen=True)

    product_type: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
