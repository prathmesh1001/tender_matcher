"""Queries for retrieval chunks."""

import uuid

from sqlalchemy import delete, func, select

from app.db.models.chunk import Chunk
from app.db.models.document import Document
from app.db.models.embedding import TextEmbedding
from app.db.models.tender import TenderDocument
from app.db.repositories.base import SqlRepository
from app.schemas.enums import DocumentSource


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

    def list_for_product(self, manufacturer: str, category: str, product_type: str) -> list[Chunk]:
        """A manufacturer's chunks that can be evidence for one product type.

        That is chunks tagged with the type, plus untagged ("general") chunks of documents that
        declare the type. Chunks of other products are never returned (B1/B3), and a document
        covering the type is never dropped because one of its chunks could not be tagged.

        Args:
            manufacturer: Exact manufacturer name.
            category: Ontology category.
            product_type: Ontology product type, e.g. "butterfly_valve".

        Returns:
            Chunks in a stable order (document, page, position).
        """
        general = Chunk.product_type.is_(None) & Document.product_types.contains([product_type])
        stmt = (
            select(Chunk)
            .join(Document, Chunk.document_id == Document.id)
            .where(
                Document.manufacturer == manufacturer,
                Document.category == category,
                (Chunk.product_type == product_type) | general,
            )
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

    def documents_needing_embedding(
        self,
        model_name: str,
        *,
        source_kind: DocumentSource | None = None,
        manufacturer: str | None = None,
    ) -> list[tuple[uuid.UUID, str]]:
        """Documents with at least one chunk that has no vector for `model_name` yet.

        Args:
            model_name: Embedding model identifier.
            source_kind: Only manufacturer or only tender documents.
            manufacturer: Only this manufacturer's documents.

        Returns:
            `(document_id, file_path)` pairs ordered by file path.
        """
        joined = (TextEmbedding.text_hash == Chunk.embed_hash) & (TextEmbedding.model == model_name)
        stmt = (
            select(Document.id, Document.file_path)
            .join(Chunk, Chunk.document_id == Document.id)
            .outerjoin(TextEmbedding, joined)
            .where(TextEmbedding.id.is_(None))
            .distinct()
            .order_by(Document.file_path)
        )
        if source_kind is not None:
            stmt = stmt.where(Document.source_kind == source_kind)
        if manufacturer is not None:
            stmt = stmt.where(Document.manufacturer == manufacturer)
        return [(doc_id, path) for doc_id, path in self._session.execute(stmt)]

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
