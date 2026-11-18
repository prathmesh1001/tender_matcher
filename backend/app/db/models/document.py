"""Stored source documents (manufacturer catalogues and tender files) and their pages."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import DocType, DocumentSource

if TYPE_CHECKING:
    from app.db.models.chunk import Chunk
    from app.db.models.section import DocumentSection


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A parsed source file, unique by content hash.

    Manufacturer documents come from `sources.yaml` and always carry manufacturer, category,
    doc type and URL. Tender documents (bid.pdf and the files it links to) are tied to their
    tenders through `TenderDocument`, so a spec shared by two tenders is parsed once.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "source_kind <> 'manufacturer' OR (manufacturer IS NOT NULL AND category IS NOT NULL"
            " AND doc_type IS NOT NULL AND url IS NOT NULL)",
            name="manufacturer_fields",
        ),
    )

    source_kind: Mapped[DocumentSource] = mapped_column(str_enum(DocumentSource), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(128), index=True)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[DocType | None] = mapped_column(str_enum(DocType))
    url: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(Text, comment="FileStore reference.")
    product_types: Mapped[list[str]] = mapped_column(
        default=list, comment="Declared in sources.yaml; empty for tender files."
    )
    sub_types: Mapped[list[str]] = mapped_column(default=list)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    fetched_at: Mapped[datetime]
    publish_date: Mapped[date | None]
    page_count: Mapped[int] = mapped_column(default=0)
    auto_sourced: Mapped[bool] = mapped_column(
        default=False,
        server_default="false",
        comment="Found by the system (GeM Marketplace), not registered and checked by hand.",
    )
    withdrawn_reason: Mapped[str | None] = mapped_column(
        Text,
        comment="Why an auto-sourced listing is no longer used (a later check rejected it); "
        "kept so earlier reports still resolve their evidence.",
    )

    pages: Mapped[list[Page]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.page_number"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    sections: Mapped[list[DocumentSection]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentSection.position"
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
