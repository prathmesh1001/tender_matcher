"""Generic repository with the CRUD operations every aggregate needs.

Repositories flush (so generated IDs and constraint errors surface immediately) but never
commit; the caller's unit of work owns the transaction.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models.base import Base


class SqlRepository[ModelT: Base]:
    """CRUD for one ORM model. Subclasses set `model` and add aggregate-specific queries."""

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, entity: ModelT) -> ModelT:
        """Stage `entity` for insert and flush it.

        Args:
            entity: A new ORM instance.

        Returns:
            The same instance, with its primary key populated.
        """
        self._session.add(entity)
        self._session.flush()
        return entity

    def add_all(self, entities: Sequence[ModelT]) -> list[ModelT]:
        """Stage several entities for insert in one flush.

        Args:
            entities: New ORM instances.

        Returns:
            The same instances as a list.
        """
        self._session.add_all(entities)
        self._session.flush()
        return list(entities)

    def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Return the entity with this primary key, or None."""
        return self._session.get(self.model, entity_id)

    def get_or_raise(self, entity_id: uuid.UUID) -> ModelT:
        """Return the entity with this primary key.

        Args:
            entity_id: Primary key.

        Returns:
            The entity.

        Raises:
            NotFoundError: If no row has this key.
        """
        entity = self.get(entity_id)
        if entity is None:
            raise NotFoundError(
                f"{self.model.__name__} {entity_id} not found", details={"id": str(entity_id)}
            )
        return entity

    def delete(self, entity: ModelT) -> None:
        """Delete `entity` (and anything its relationships cascade to) and flush."""
        self._session.delete(entity)
        self._session.flush()
