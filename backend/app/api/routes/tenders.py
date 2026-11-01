"""Tender routes."""

from fastapi import APIRouter, UploadFile, status

from app.api.deps import TenderServiceDep
from app.schemas.tenders import TenderUploadResponse

router = APIRouter(prefix="/tenders", tags=["tenders"])


@router.post("", response_model=TenderUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_tender(file: UploadFile, service: TenderServiceDep) -> TenderUploadResponse:
    """Store a GeM bid.pdf. Linked files are fetched when a run starts."""
    return service.upload(file.filename or "", file.file.read())
