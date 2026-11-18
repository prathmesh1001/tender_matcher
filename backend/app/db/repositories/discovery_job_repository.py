"""Queries for GeM Marketplace discovery jobs."""

import uuid

from sqlalchemy import select

from app.db.models.discovery_job import DiscoveryJob
from app.db.repositories.base import SqlRepository


class DiscoveryJobRepository(SqlRepository[DiscoveryJob]):
    """Discovery jobs by run."""

    model = DiscoveryJob

    def list_for_run(self, run_id: uuid.UUID) -> list[DiscoveryJob]:
        """Every job asked for a run, newest first."""
        stmt = (
            select(DiscoveryJob)
            .where(DiscoveryJob.run_id == run_id)
            .order_by(DiscoveryJob.created_at.desc(), DiscoveryJob.id.desc())
        )
        return list(self._session.scalars(stmt))
