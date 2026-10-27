"""Manufacturer documents and their pages."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import DocType

if TYPE_CHECKING:
    from app.db.models.chunk import Chunk


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A catalogue/datasheet/brochure registered in `sources.yaml`. Unique by content hash."""

    __tablename__ = "documents"

    manufacturer: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[DocType] = mapped_column(str_enum(DocType))
    url: Mapped[str] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    fetched_at: Mapped[datetime]
    publish_date: Mapped[date | None]
    page_count: Mapped[int] = mapped_column(default=0)

    pages: Mapped[list[Page]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.page_number"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Page(UUIDPrimaryKeyMixin, Base):
    """Extracted text of one page (1-based `page_number`, as printed in citations)."""

    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    ocr: Mapped[bool] = mapped_column(default=False)
    char_count: Mapped[int] = mapped_column(default=0)

    document: Mapped[Document] = relationship(back_populates="pages")
