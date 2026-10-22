"""Health routes."""

from fastapi import APIRouter, status

from app.api.deps import HealthServiceDep
from app.schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def get_health(service: HealthServiceDep) -> HealthResponse:
    """Liveness check: returns `ok` with app name, version and environment."""
    return service.get_status()
