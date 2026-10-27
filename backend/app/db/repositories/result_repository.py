"""Queries for match results and their evidence."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models.result import MatchResult
from app.db.repositories.base import SqlRepository


class ResultRepository(SqlRepository[MatchResult]):
    """Results are written with their evidence attached via `MatchResult.evidence`."""

    model = MatchResult

    def list_for_run(
        self, run_id: uuid.UUID, *, manufacturer: str | None = None
    ) -> list[MatchResult]:
        """Return a run's results with evidence eagerly loaded (no N+1 when building reports).

        Args:
            run_id: The run.
            manufacturer: Only results for this manufacturer.

        Returns:
            Results ordered by manufacturer, then requirement.
        """
        stmt = (
            select(MatchResult)
            .where(MatchResult.run_id == run_id)
            .options(selectinload(MatchResult.evidence))
            .order_by(MatchResult.manufacturer, MatchResult.requirement_id)
        )
        if manufacturer is not None:
            stmt = stmt.where(MatchResult.manufacturer == manufacturer)
        return list(self._session.scalars(stmt))
