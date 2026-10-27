"""Queries for retrieval chunks."""

import uuid

from sqlalchemy import delete, func, select

from app.db.models.chunk import Chunk
from app.db.models.document import Document
from app.db.models.tender import TenderDocument
from app.db.repositories.base import SqlRepository


class ChunkRepository(SqlRepository[Chunk]):
    """Chunks of manufacturer and tender document pages."""

    model = Chunk

    def list_for_document(self, document_id: uuid.UUID) -> list[Chunk]:
        """Return a document's chunks in reading order (page, then position)."""
        stmt = (
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.page_number, Chunk.position)
        )
        return list(self._session.scalars(stmt))

    def list_for_manufacturer(self, manufacturer: str, category: str) -> list[Chunk]:
        """Return every chunk from one manufacturer's documents in a category.

        Args:
            manufacturer: Exact manufacturer name.
            category: Exact product category.

        Returns:
            Chunks in a stable order (document, page, position).
        """
        stmt = (
            select(Chunk)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.manufacturer == manufacturer, Document.category == category)
            .order_by(Chunk.document_id, Chunk.page_number, Chunk.position)
        )
        return list(self._session.scalars(stmt))

    def list_for_tender(self, tender_id: uuid.UUID) -> list[Chunk]:
        """Return every chunk of every document linked to a tender (bid.pdf and attachments).

        Args:
            tender_id: The tender.

        Returns:
            Chunks in a stable order (document, page, position).
        """
        stmt = (
            select(Chunk)
            .join(TenderDocument, Chunk.document_id == TenderDocument.document_id)
            .where(TenderDocument.tender_id == tender_id)
            .order_by(Chunk.document_id, Chunk.page_number, Chunk.position)
        )
        return list(self._session.scalars(stmt))

    def count_for_document(self, document_id: uuid.UUID) -> int:
        """Return how many chunks a document has."""
        stmt = select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        return int(self._session.scalar(stmt) or 0)

    def delete_for_document(self, document_id: uuid.UUID) -> int:
        """Delete all chunks of a document (before re-chunking it).

        Args:
            document_id: The document whose chunks to remove.

        Returns:
            Number of rows deleted.
        """
        result = self._session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        self._session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
