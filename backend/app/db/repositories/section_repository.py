"""Queries for document sections."""

import uuid

from sqlalchemy import delete, select, update

from app.db.models.chunk import Chunk
from app.db.models.section import DocumentSection
from app.db.repositories.base import SqlRepository


class SectionRepository(SqlRepository[DocumentSection]):
    """Sections of stored documents."""

    model = DocumentSection

    def list_for_document(self, document_id: uuid.UUID) -> list[DocumentSection]:
        """Return a document's sections in document order."""
        stmt = (
            select(DocumentSection)
            .where(DocumentSection.document_id == document_id)
            .order_by(DocumentSection.position)
        )
        return list(self._session.scalars(stmt))

    def delete_for_document(self, document_id: uuid.UUID) -> int:
        """Detach a document's chunks from their sections and delete the sections.

        Args:
            document_id: The document whose sections to remove.

        Returns:
            Number of sections deleted.
        """
        self._session.execute(
            update(Chunk).where(Chunk.document_id == document_id).values(section_id=None)
        )
        result = self._session.execute(
            delete(DocumentSection).where(DocumentSection.document_id == document_id)
        )
        self._session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
