"""Report routes."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import ReportServiceDep
from app.schemas.report import TenderReport

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{run_id}", response_model=TenderReport, status_code=status.HTTP_200_OK)
def get_report(run_id: uuid.UUID, service: ReportServiceDep) -> TenderReport:
    """The finished report of a run; 409 with the status and reason if there is none."""
    return service.get(run_id)
