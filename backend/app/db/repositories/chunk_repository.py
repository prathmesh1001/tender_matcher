"""Queries for retrieval chunks."""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select

from app.db.models.chunk import Chunk
from app.db.models.document import Document
from app.db.models.embedding import TextEmbedding
from app.db.models.spec_fact import SpecFact
from app.db.models.tender import TenderDocument
from app.db.repositories.base import SqlRepository
from app.schemas.enums import DocumentSource
from app.schemas.retrieval import DocumentIndexStatus


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

    def list_by_ids(self, ids: Sequence[uuid.UUID]) -> list[Chunk]:
        """Return the chunks with these ids, in no particular order."""
        return list(self._session.scalars(select(Chunk).where(Chunk.id.in_(ids))))

    def list_for_product(
        self, manufacturer: str, category: str, product_type: str, *, sha256: str | None = None
    ) -> list[Chunk]:
        """A manufacturer's chunks that can be evidence for one product type.

        That is chunks tagged with the type, plus untagged ("general") chunks of documents that
        declare the type. Chunks of other products are never returned (B1/B3), and a document
        covering the type is never dropped because one of its chunks could not be tagged.

        Args:
            manufacturer: Exact manufacturer name.
            category: Ontology category.
            product_type: Ontology product type, e.g. "butterfly_valve".
            sha256: Only this document; None for all of them.

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
        if sha256 is not None:
            stmt = stmt.where(Document.sha256 == sha256)
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

    def index_status(self, model_name: str) -> list[DocumentIndexStatus]:
        """Chunks, vectors and spec facts of every stored catalogue.

        Args:
            model_name: Embedding model identifier; vectors of other models do not count.

        Returns:
            One row per manufacturer document, ordered by file path.
        """
        joined = (TextEmbedding.text_hash == Chunk.embed_hash) & (TextEmbedding.model == model_name)
        facts = (
            select(func.count(SpecFact.id))
            .where(SpecFact.document_id == Document.id)
            .scalar_subquery()
        )
        stmt = (
            select(Document.file_path, func.count(Chunk.id), func.count(TextEmbedding.id), facts)
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .outerjoin(TextEmbedding, joined)
            .where(Document.source_kind == DocumentSource.MANUFACTURER)
            .group_by(Document.id, Document.file_path)
            .order_by(Document.file_path)
        )
        return [
            DocumentIndexStatus(file_path=path, chunks=chunks, with_vector=vectors, spec_facts=n)
            for path, chunks, vectors, n in self._session.execute(stmt)
        ]

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
