"""Queries for tenders, their linked documents and their requirements."""

import uuid

from sqlalchemy import select

from app.db.models.tender import Requirement, Tender, TenderDocument
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

    def get_by_bid_number(self, bid_number: str) -> Tender | None:
        """Return the tender with this GeM bid number (e.g. "GEM/2026/B/8119613"), or None."""
        return self._session.scalar(select(Tender).where(Tender.bid_number == bid_number))

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
