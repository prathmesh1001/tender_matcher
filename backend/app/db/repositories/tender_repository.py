"""Queries for tenders and their requirements."""

import uuid

from sqlalchemy import select

from app.db.models.tender import Requirement, Tender
from app.db.repositories.base import SqlRepository


class TenderRepository(SqlRepository[Tender]):
    """Tenders are the aggregate root; requirements are written through `Tender.requirements`."""

    model = Tender

    def get_by_sha256(self, sha256: str) -> Tender | None:
        """Return the tender with this content hash (re-uploads reuse it), or None."""
        return self._session.scalar(select(Tender).where(Tender.sha256 == sha256))

    def list_recent(self, *, limit: int, offset: int = 0) -> list[Tender]:
        """Return tenders, newest first.

        Args:
            limit: Maximum number of rows.
            offset: Rows to skip, for pagination.

        Returns:
            Tenders ordered by creation time, descending.
        """
        # id (UUIDv7, time-ordered) breaks ties between rows created in the same transaction.
        stmt = (
            select(Tender)
            .order_by(Tender.created_at.desc(), Tender.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(stmt))

    def list_requirements(self, tender_id: uuid.UUID) -> list[Requirement]:
        """Return every requirement (parents and atomic children) of a tender, in order."""
        stmt = (
            select(Requirement)
            .where(Requirement.tender_id == tender_id)
            .order_by(Requirement.position)
        )
        return list(self._session.scalars(stmt))
