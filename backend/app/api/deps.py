"""FastAPI dependency providers.

Routes obtain settings and services only through these functions, so tests can swap them with
`app.dependency_overrides`.
"""

from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.health_service import HealthService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_health_service(settings: SettingsDep) -> HealthService:
    """Provide a `HealthService` bound to the active settings.

    Args:
        settings: Injected application settings.

    Returns:
        A health service instance.
    """
    return HealthService(settings)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
