"""Tenders, the documents they link to, and their atomic requirements."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import Operator, RequirementType

if TYPE_CHECKING:
    from app.db.models.document import Document


class Tender(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A GeM bid, identified by its bid.pdf. Unique by bid.pdf hash so re-uploads reuse it."""

    __tablename__ = "tenders"

    filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    bid_number: Mapped[str | None] = mapped_column(String(128))
    gem_bid_id: Mapped[str | None] = mapped_column(String(32))
    page_count: Mapped[int] = mapped_column(default=0)

    documents: Mapped[list[TenderDocument]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )

    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="Requirement.position"
    )


class TenderDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A file belonging to a tender: bid.pdf itself or a document linked from it."""

    __tablename__ = "tender_documents"
    __table_args__ = (UniqueConstraint("tender_id", "document_id"),)

    tender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(64), comment="bid_document, buyer_spec, atc, ...")
    url: Mapped[str | None] = mapped_column(Text, comment="Null for user-provided copies.")
    found_in: Mapped[str | None] = mapped_column(String(256), comment="File holding the link.")
    depth: Mapped[int | None] = mapped_column(comment="Link depth from bid.pdf (0 = bid.pdf).")

    tender: Mapped[Tender] = relationship(back_populates="documents")
    document: Mapped[Document] = relationship()


class Requirement(UUIDPrimaryKeyMixin, Base):
    """One requirement line. Compound lines are parents; their atomic parts point to them."""

    __tablename__ = "requirements"
    __table_args__ = (Index("ix_requirements_tender_position", "tender_id", "position"),)

    tender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(comment="Order in the tender.")
    page_number: Mapped[int]
    raw_text: Mapped[str] = mapped_column(Text)
    req_type: Mapped[RequirementType] = mapped_column(str_enum(RequirementType))
    attribute: Mapped[str | None] = mapped_column(String(128))
    operator: Mapped[Operator | None] = mapped_column(str_enum(Operator))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_num: Mapped[float | None] = mapped_column(Float)
    value_min: Mapped[float | None] = mapped_column(Float)
    value_max: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    weight: Mapped[float] = mapped_column(Float)

    tender: Mapped[Tender] = relationship(back_populates="requirements")
    parent: Mapped[Requirement | None] = relationship(
        remote_side="Requirement.id", back_populates="children"
    )
    children: Mapped[list[Requirement]] = relationship(
        back_populates="parent", order_by="Requirement.position"
    )
