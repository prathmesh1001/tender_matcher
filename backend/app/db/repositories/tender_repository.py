"""Queries for tenders and their linked documents."""

import uuid

from sqlalchemy import func, select

from app.db.models.tender import Tender, TenderDocument
from app.db.repositories.base import SqlRepository


class TenderRepository(SqlRepository[Tender]):
    """Tenders and the files linked to them; what a run read from them is on `Run`."""

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

    def latest_revision(self, bid_number: str) -> int:
        """Return the highest stored revision of this GeM bid number, or 0 if it has none."""
        stmt = select(func.max(Tender.revision)).where(Tender.bid_number == bid_number)
        return self._session.scalar(stmt) or 0

    def linked_document_ids(self, tender_id: uuid.UUID) -> set[uuid.UUID]:
        """Return the ids of every document already linked to a tender."""
        stmt = select(TenderDocument.document_id).where(TenderDocument.tender_id == tender_id)
        return set(self._session.scalars(stmt))

    def add_document_link(self, link: TenderDocument) -> TenderDocument:
        """Link a stored document to a tender and flush.

        Args:
            link: A new tender-document link.

        Returns:
            The same link, with its id populated.
        """
        self._session.add(link)
        self._session.flush()
        return link

    def list_documents(self, tender_id: uuid.UUID) -> list[TenderDocument]:
        """Return a tender's document links ordered by link depth, then role."""
        stmt = (
            select(TenderDocument)
            .where(TenderDocument.tender_id == tender_id)
            .order_by(TenderDocument.depth.nulls_first(), TenderDocument.role)
        )
        return list(self._session.scalars(stmt))
