"""Queries for structured specification facts."""

import uuid

from sqlalchemy import select

from app.db.models.spec_fact import SpecFact
from app.db.repositories.base import SqlRepository


class SpecFactRepository(SqlRepository[SpecFact]):
    """Verified attribute/value facts used for the structured lookup stage of matching."""

    model = SpecFact

    def find(self, *, manufacturer: str, category: str, attribute: str) -> list[SpecFact]:
        """Return all facts for one canonical attribute of one manufacturer.

        Conflicting facts are all returned; resolving them is the matcher's job.

        Args:
            manufacturer: Exact manufacturer name.
            category: Exact product category.
            attribute: Canonical ontology attribute, e.g. `body_material`.

        Returns:
            Facts ordered by product family, model and page.
        """
        stmt = (
            select(SpecFact)
            .where(
                SpecFact.manufacturer == manufacturer,
                SpecFact.category == category,
                SpecFact.attribute == attribute,
            )
            .order_by(SpecFact.product_family, SpecFact.model, SpecFact.page_number)
        )
        return list(self._session.scalars(stmt))

    def list_for_document(self, document_id: uuid.UUID) -> list[SpecFact]:
        """Return every fact extracted from one document, by page."""
        stmt = (
            select(SpecFact)
            .where(SpecFact.document_id == document_id)
            .order_by(SpecFact.page_number, SpecFact.attribute)
        )
        return list(self._session.scalars(stmt))
