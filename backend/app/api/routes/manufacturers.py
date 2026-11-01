"""Manufacturer routes."""

from fastapi import APIRouter, status

from app.api.deps import ManufacturerServiceDep
from app.schemas.manufacturers import Manufacturer

router = APIRouter(prefix="/manufacturers", tags=["manufacturers"])


@router.get("", response_model=list[Manufacturer], status_code=status.HTTP_200_OK)
def list_manufacturers(service: ManufacturerServiceDep) -> list[Manufacturer]:
    """Manufacturers with registered catalogues, and the product types those cover."""
    return service.list_manufacturers()
