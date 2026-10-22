"""Health use case."""

from app.core.config import Settings
from app.schemas.health import HealthResponse


class HealthService:
    """Report the liveness of the backend.

    Dependency checks (DB, Redis) are added here once those layers exist.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_status(self) -> HealthResponse:
        """Return the current health status.

        Returns:
            Liveness info with app name, version and environment.
        """
        return HealthResponse(
            status="ok",
            app=self._settings.APP_NAME,
            version=self._settings.APP_VERSION,
            environment=self._settings.ENVIRONMENT,
        )
