"""Retrieval chunks (prose sections or single table rows) cut from document pages."""

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import ChunkType

if TYPE_CHECKING:
    from app.db.models.document import Document, Page
    from app.db.models.section import DocumentSection


class Chunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A searchable piece of one page (manufacturer or tender document). Embeddings come later."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_document_page", "document_id", "page_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int]
    position: Mapped[int] = mapped_column(comment="Order of the chunk within its page.")
    chunk_type: Mapped[ChunkType] = mapped_column(str_enum(ChunkType))
    raw_text: Mapped[str] = mapped_column(Text, comment="Verbatim; quotes come from here.")
    search_text: Mapped[str] = mapped_column(Text, comment="normalize_for_search(raw_text).")
    section: Mapped[str | None] = mapped_column(Text)
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_sections.id", ondelete="SET NULL"), index=True
    )
    section_category: Mapped[str | None] = mapped_column(
        String(64), index=True, comment="config/section_categories.yaml; NULL = not classified."
    )
    product_family: Mapped[str | None] = mapped_column(Text)
    product_type: Mapped[str | None] = mapped_column(
        String(64), index=True, comment="Ontology product type; NULL = general."
    )
    product_sub_type: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(default=0)
    embed_hash: Mapped[str | None] = mapped_column(
        String(64), index=True, comment="sha256 of embedding_text(raw_text); joins text_embeddings."
    )
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict, comment="bbox, table header, ...")

    document: Mapped[Document] = relationship(back_populates="chunks")
    page: Mapped[Page] = relationship()
    document_section: Mapped[DocumentSection | None] = relationship(back_populates="chunks")
