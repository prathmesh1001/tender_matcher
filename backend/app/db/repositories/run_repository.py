"""Queries for matching runs."""

import uuid

from sqlalchemy import select

from app.db.models.run import Run
from app.db.repositories.base import SqlRepository
from app.schemas.enums import RunStatus


class RunRepository(SqlRepository[Run]):
    """Persistence for runs. Status transition rules live in the run service, not here."""

    model = Run

    def list_recent(
        self,
        *,
        limit: int,
        offset: int = 0,
        tender_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> list[Run]:
        """Return runs newest first, optionally filtered.

        Args:
            limit: Maximum number of rows.
            offset: Rows to skip, for pagination.
            tender_id: Only runs for this tender.
            status: Only runs in this status.

        Returns:
            Matching runs ordered by creation time, descending.
        """
        stmt = select(Run).order_by(Run.created_at.desc(), Run.id.desc())
        if tender_id is not None:
            stmt = stmt.where(Run.tender_id == tender_id)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        return list(self._session.scalars(stmt.limit(limit).offset(offset)))
