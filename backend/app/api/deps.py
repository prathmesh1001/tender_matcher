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
from app.services.manufacturer_service import (
    ManufacturerService,
    build_manufacturer_service,
)
from app.services.report_service import ReportService, build_report_service
from app.services.run_service import RunService
from app.services.tender_service import TenderService
from app.storage.file_store import build_file_store

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


def get_manufacturer_service(settings: SettingsDep) -> ManufacturerService:
    """Provide a `ManufacturerService` over the catalogue registry.

    Args:
        settings: Injected application settings.

    Returns:
        A manufacturer service.
    """
    return build_manufacturer_service(settings)


ManufacturerServiceDep = Annotated[ManufacturerService, Depends(get_manufacturer_service)]


def get_tender_service(request: Request, settings: SettingsDep) -> TenderService:
    """Provide a `TenderService`; it commits its own transaction.

    Args:
        request: The current request; its app holds the session factory.
        settings: Injected application settings.

    Returns:
        A tender service.
    """
    return TenderService(
        request.app.state.session_factory,
        build_file_store(settings),
        settings.TENDER_DATA_DIR,
        settings.TENDER_UPLOAD_MAX_MB,
    )


TenderServiceDep = Annotated[TenderService, Depends(get_tender_service)]


def get_run_service(request: Request) -> RunService:
    """Provide the app's one `RunService`, which owns the job queue.

    Args:
        request: The current request; its app holds the service.

    Returns:
        The run service.
    """
    service: RunService = request.app.state.run_service
    return service


RunServiceDep = Annotated[RunService, Depends(get_run_service)]


def get_report_service(session: DbSessionDep, settings: SettingsDep) -> ReportService:
    """Provide a `ReportService` bound to the request's session.

    Args:
        session: Injected session.
        settings: Injected application settings.

    Returns:
        A report service.
    """
    return build_report_service(session, settings)


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
