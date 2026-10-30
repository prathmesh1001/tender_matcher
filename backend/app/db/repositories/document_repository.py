"""Queries for stored documents (manufacturer and tender) and their pages."""

import uuid

from sqlalchemy import delete, select

from app.db.models.document import Document, Page
from app.db.repositories.base import SqlRepository
from app.schemas.enums import DocumentSource


class DocumentRepository(SqlRepository[Document]):
    """Documents are the aggregate root; pages are written through `Document.pages`."""

    model = Document

    def get_by_sha256(self, sha256: str) -> Document | None:
        """Return the document with this content hash (used for idempotent ingestion)."""
        return self._session.scalar(select(Document).where(Document.sha256 == sha256))

    def find(
        self,
        *,
        manufacturer: str | None = None,
        category: str | None = None,
        source_kind: DocumentSource | None = None,
    ) -> list[Document]:
        """Find documents, optionally filtered, ordered by manufacturer then title.

        Args:
            manufacturer: Exact manufacturer name to filter by.
            category: Exact category to filter by.
            source_kind: Only manufacturer or only tender documents.

        Returns:
            Matching documents.
        """
        stmt = select(Document).order_by(Document.manufacturer, Document.title)
        if source_kind is not None:
            stmt = stmt.where(Document.source_kind == source_kind)
        if manufacturer is not None:
            stmt = stmt.where(Document.manufacturer == manufacturer)
        if category is not None:
            stmt = stmt.where(Document.category == category)
        return list(self._session.scalars(stmt))

    def list_manufacturers(self, category: str) -> list[str]:
        """Return the distinct manufacturers that have documents in `category`, sorted."""
        stmt = (
            select(Document.manufacturer)
            .where(Document.category == category)
            .distinct()
            .order_by(Document.manufacturer)
        )
        return list(self._session.scalars(stmt))

    def get_page(self, document_id: uuid.UUID, page_number: int) -> Page | None:
        """Return one page of a document by its 1-based page number, or None."""
        stmt = select(Page).where(Page.document_id == document_id, Page.page_number == page_number)
        return self._session.scalar(stmt)

    def delete_pages(self, document_id: uuid.UUID) -> int:
        """Delete all pages of a document (before re-parsing it; delete its chunks first).

        Args:
            document_id: The document whose pages to remove.

        Returns:
            Number of rows deleted.
        """
        result = self._session.execute(delete(Page).where(Page.document_id == document_id))
        self._session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
