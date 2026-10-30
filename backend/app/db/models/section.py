"""Document sections: runs of chunks under one heading, with a normalized category."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.sections import CategorySource

if TYPE_CHECKING:
    from app.db.models.chunk import Chunk
    from app.db.models.document import Document


class DocumentSection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One section of a stored document (rebuilt whenever its chunks are)."""

    __tablename__ = "document_sections"
    __table_args__ = (
        UniqueConstraint("document_id", "position"),
        Index("ix_document_sections_document_category", "document_id", "category"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_sections.id", ondelete="SET NULL"), index=True
    )
    position: Mapped[int] = mapped_column(comment="Order of the section in the document.")
    title: Mapped[str | None] = mapped_column(Text, comment="Heading as printed; NULL = none.")
    number: Mapped[str | None] = mapped_column(String(32), comment='Heading number, e.g. "10.1".')
    category: Mapped[str] = mapped_column(String(64), comment="config/section_categories.yaml")
    category_source: Mapped[CategorySource] = mapped_column(str_enum(CategorySource))
    start_page: Mapped[int]
    end_page: Mapped[int]
    chunk_count: Mapped[int] = mapped_column(default=0)

    document: Mapped[Document] = relationship(back_populates="sections")
    parent: Mapped[DocumentSection | None] = relationship(remote_side="DocumentSection.id")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document_section")
