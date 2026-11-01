"""Queries for matching runs."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.db.models.run import Run
from app.db.models.tender import LineItem, Requirement
from app.db.repositories.base import SqlRepository
from app.schemas.enums import RunStatus


class RunRepository(SqlRepository[Run]):
    """Runs are the aggregate root: line items and requirements are written through `Run`.

    Status transition rules live in the run service, not here.
    """

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

    def list_unfinished(self) -> list[Run]:
        """Return every run still queued or running."""
        stmt = select(Run).where(Run.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)))
        return list(self._session.scalars(stmt))

    def add_rows(self, line_items: Sequence[LineItem], requirements: Sequence[Requirement]) -> None:
        """Stage what a run read and flush. Parent requirements must come before their parts.

        Args:
            line_items: New line items, with `run_id` set.
            requirements: New requirements, with `run_id` and any links set.
        """
        self._session.add_all(line_items)
        self._session.add_all(requirements)
        self._session.flush()

    def flush(self) -> None:
        """Write pending changes to a run, so constraint errors surface now."""
        self._session.flush()

    def list_line_items(self, run_id: uuid.UUID) -> list[LineItem]:
        """Return the line items a run read, in order."""
        stmt = select(LineItem).where(LineItem.run_id == run_id).order_by(LineItem.position)
        return list(self._session.scalars(stmt))

    def list_requirements(self, run_id: uuid.UUID) -> list[Requirement]:
        """Return every requirement of a run (parents, atomic parts, general ones), in order."""
        stmt = (
            select(Requirement).where(Requirement.run_id == run_id).order_by(Requirement.position)
        )
        return list(self._session.scalars(stmt))
