"""FastAPI dependency providers.

Routes obtain settings, DB sessions and services only through these functions, so tests can
swap them with `app.dependency_overrides`.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import session_scope
from app.services.health_service import HealthService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_db_session(request: Request) -> Iterator[Session]:
    """Provide one transactional session per request (commit on success, rollback on error).

    Args:
        request: The current request; its app holds the session factory.

    Yields:
        An open session.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    with session_scope(factory) as session:
        yield session


DbSessionDep = Annotated[Session, Depends(get_db_session)]


def get_health_service(settings: SettingsDep) -> HealthService:
    """Provide a `HealthService` bound to the active settings.

    Args:
        settings: Injected application settings.

    Returns:
        A health service instance.
    """
    return HealthService(settings)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
