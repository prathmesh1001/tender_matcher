"""Application factory.

Run with: `uvicorn app.main:create_app --factory`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import create_db_engine, create_session_factory
from app.services.run_service import build_run_service
from app.workers.tasks import ThreadJobQueue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fail lost runs and warm the catalogue cache; on shutdown stop the queue and the DB."""
    try:
        lost = app.state.run_service.fail_interrupted()
        if lost:
            logger.warning("marked interrupted runs failed", extra={"runs": lost})
    except SQLAlchemyError as exc:  # the API still starts without a DB; requests then report it
        logger.warning("could not check for interrupted runs", extra={"error": str(exc)})
    app.state.run_service.warm_catalogues()
    yield
    app.state.job_queue.shutdown()
    app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: Settings to use. Defaults to the environment-loaded settings. When given, they
            also override the `get_settings` dependency so routes see the same values.

    Returns:
        The configured application.
    """
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL, json_output=settings.LOG_JSON)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        openapi_url=f"{settings.API_PREFIX}/openapi.json",
        docs_url=f"{settings.API_PREFIX}/docs",
        swagger_ui_oauth2_redirect_url=f"{settings.API_PREFIX}/docs/oauth2-redirect",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    # The engine connects lazily, so building the app never requires a reachable DB.
    app.state.engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.job_queue = ThreadJobQueue()
    app.state.run_service = build_run_service(
        settings, app.state.session_factory, app.state.job_queue
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_PREFIX)
    return app
