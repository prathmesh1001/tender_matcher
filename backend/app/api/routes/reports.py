"""Report routes."""

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import ReportServiceDep
from app.reporting.excel_report import excel_report
from app.schemas.report import TenderReport

EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{run_id}", response_model=TenderReport, status_code=status.HTTP_200_OK)
def get_report(run_id: uuid.UUID, service: ReportServiceDep) -> TenderReport:
    """The finished report of a run; 409 with the status and reason if there is none."""
    return service.get(run_id)


@router.get(
    "/{run_id}/excel",
    response_class=Response,
    response_model=None,
    status_code=status.HTTP_200_OK,
)
def get_report_excel(run_id: uuid.UUID, service: ReportServiceDep) -> Response:
    """The finished report as an Excel workbook; 409 with the status and reason if there is none."""
    report = service.get(run_id)
    return Response(
        content=excel_report(report),
        media_type=EXCEL_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{report.tender}_{run_id}.xlsx"'},
    )
