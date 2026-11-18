"""Discovery routes: GeM Marketplace listings for product types no document covers."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import DiscoveryServiceDep
from app.schemas.discovery import DiscoveryJobResponse, DiscoveryOverview, StartDiscoveryRequest

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/runs/{run_id}", response_model=DiscoveryOverview, status_code=status.HTTP_200_OK)
def run_overview(run_id: uuid.UUID, service: DiscoveryServiceDep) -> DiscoveryOverview:
    """Whether discovery is on, the run's uncovered product types and their GeM checks."""
    return service.overview(run_id)


@router.post(
    "/runs/{run_id}/gem",
    response_model=DiscoveryJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_gem_check(
    run_id: uuid.UUID, request: StartDiscoveryRequest, service: DiscoveryServiceDep
) -> DiscoveryJobResponse:
    """Queue a GeM Marketplace check for one uncovered product type; poll the job."""
    return service.start(run_id, request.product_type)


@router.get("/jobs/{job_id}", response_model=DiscoveryJobResponse, status_code=status.HTTP_200_OK)
def get_job(job_id: uuid.UUID, service: DiscoveryServiceDep) -> DiscoveryJobResponse:
    """One GeM check: listings read, why each was kept or not, and the re-matched run."""
    return service.get(job_id)
