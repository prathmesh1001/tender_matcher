"""Queries for structured specification facts."""

import uuid

from sqlalchemy import delete, func, select, update

from app.db.models.chunk import Chunk
from app.db.models.document import Document
from app.db.models.spec_fact import SpecFact
from app.db.repositories.base import SqlRepository


class SpecFactRepository(SqlRepository[SpecFact]):
    """Verified attribute/value facts used for the structured lookup stage of matching."""

    model = SpecFact

    def find(
        self,
        *,
        manufacturer: str,
        category: str,
        attribute: str,
        product_type: str | None = None,
    ) -> list[SpecFact]:
        """Return all facts for one canonical attribute of one manufacturer.

        Conflicting facts are all returned; resolving them is the matcher's job. With a
        product type, the rule is the one of `ChunkRepository.list_for_product`: facts tagged
        with the type, plus untagged ("general") facts of documents that declare it. Facts of
        other products (a pump's pressure for a gate valve) are never returned.

        Args:
            manufacturer: Exact manufacturer name.
            category: Exact product category.
            attribute: Canonical ontology attribute, e.g. `body_material`.
            product_type: Ontology product type, e.g. "gate_valve"; None for every product.

        Returns:
            Facts ordered by product family, model and page.
        """
        stmt = select(SpecFact).where(
            SpecFact.manufacturer == manufacturer,
            SpecFact.category == category,
            SpecFact.attribute == attribute,
        )
        if product_type is not None:
            general = SpecFact.product_type.is_(None) & Document.product_types.contains(
                [product_type]
            )
            stmt = stmt.join(Document, SpecFact.document_id == Document.id).where(
                (SpecFact.product_type == product_type) | general
            )
        stmt = stmt.order_by(SpecFact.product_family, SpecFact.model, SpecFact.page_number)
        return list(self._session.scalars(stmt))

    def sync_products_from_chunks(self, document_id: uuid.UUID) -> int:
        """Copy each fact's product type and sub-type from its chunk (after re-tagging).

        Facts without a chunk keep what they have.

        Args:
            document_id: The document whose facts to update.

        Returns:
            Number of facts whose product type or sub-type changed.
        """
        stmt = (
            update(SpecFact)
            .where(
                SpecFact.document_id == document_id,
                SpecFact.chunk_id == Chunk.id,
                (SpecFact.product_type.is_distinct_from(Chunk.product_type))
                | (SpecFact.product_sub_type.is_distinct_from(Chunk.product_sub_type)),
            )
            .values(product_type=Chunk.product_type, product_sub_type=Chunk.product_sub_type)
            .execution_options(synchronize_session=False)
        )
        result = self._session.execute(stmt)
        self._session.expire_all()
        return int(result.rowcount)  # type: ignore[attr-defined]

    def list_for_document(self, document_id: uuid.UUID) -> list[SpecFact]:
        """Return every fact extracted from one document, by page."""
        stmt = (
            select(SpecFact)
            .where(SpecFact.document_id == document_id)
            .order_by(SpecFact.page_number, SpecFact.attribute)
        )
        return list(self._session.scalars(stmt))

    def count_for_document(self, document_id: uuid.UUID) -> int:
        """Return how many facts are stored for one document."""
        stmt = select(func.count()).select_from(SpecFact).where(SpecFact.document_id == document_id)
        return int(self._session.scalar(stmt) or 0)

    def delete_for_document(self, document_id: uuid.UUID) -> int:
        """Delete every fact of one document (before storing a new extraction).

        Args:
            document_id: The document whose facts to remove.

        Returns:
            Number of rows deleted.
        """
        result = self._session.execute(delete(SpecFact).where(SpecFact.document_id == document_id))
        self._session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
