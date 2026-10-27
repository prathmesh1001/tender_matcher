"""Retrieval chunks (prose sections or single table rows) cut from document pages."""

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import ChunkType

if TYPE_CHECKING:
    from app.db.models.document import Document


class Chunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A searchable piece of one page. Embeddings are added in the indexing step."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_document_page", "document_id", "page_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int]
    position: Mapped[int] = mapped_column(comment="Order of the chunk within its page.")
    chunk_type: Mapped[ChunkType] = mapped_column(str_enum(ChunkType))
    text: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(512))
    product_family: Mapped[str | None] = mapped_column(String(256))
    model: Mapped[str | None] = mapped_column(String(256))
    token_count: Mapped[int] = mapped_column(default=0)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict, comment="bbox, table header, ...")

    document: Mapped[Document] = relationship(back_populates="chunks")
