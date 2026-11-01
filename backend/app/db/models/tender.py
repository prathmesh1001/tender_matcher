"""Tenders, the documents they link to, and what each run read from them.

Line items and requirements belong to a run, not to the tender: parsing rules change, and a
past run must keep what it actually compared. Numbers printed in a tender are not unique (one
tender can repeat its item numbers), so rows are told apart by their own id and position.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import Operator, RequirementType
from app.schemas.requirements import ValueKind
from app.schemas.sections import EvidenceSource

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.run import Run


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


class LineItem(UUIDPrimaryKeyMixin, Base):
    """One item of a tender's schedule, as one run read it."""

    __tablename__ = "line_items"
    __table_args__ = (UniqueConstraint("run_id", "position"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(comment="Order in the run.")
    item_no: Mapped[str] = mapped_column(Text, comment="As printed; can repeat within a tender.")
    title: Mapped[str] = mapped_column(Text)
    description_lines: Mapped[list[str]] = mapped_column(default=list)
    description_header: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    document: Mapped[str] = mapped_column(Text, comment="Tender file the item comes from.")
    page_number: Mapped[int]
    category: Mapped[str | None] = mapped_column(String(64), comment="Ontology category used.")
    product_type: Mapped[str | None] = mapped_column(String(128))
    sub_type: Mapped[str | None] = mapped_column(String(128))

    run: Mapped[Run] = relationship(back_populates="line_items")
    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="line_item", order_by="Requirement.position"
    )


class Requirement(UUIDPrimaryKeyMixin, Base):
    """One requirement line. Compound lines are parents; their atomic parts point to them."""

    __tablename__ = "requirements"
    __table_args__ = (Index("ix_requirements_run_position", "run_id", "position"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("line_items.id", ondelete="CASCADE"),
        index=True,
        comment="NULL = a general requirement, outside the item schedule.",
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    ref: Mapped[str] = mapped_column(Text, comment="The report's requirement id; can repeat.")
    position: Mapped[int] = mapped_column(comment="Order in the run.")
    document: Mapped[str] = mapped_column(Text, comment="Tender file the line comes from.")
    page_number: Mapped[int]
    raw_text: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text, comment="Label as printed.")
    req_type: Mapped[RequirementType] = mapped_column(str_enum(RequirementType))
    category: Mapped[str | None] = mapped_column(String(64), comment="Section category.")
    evidence: Mapped[EvidenceSource | None] = mapped_column(str_enum(EvidenceSource))
    product_type: Mapped[str | None] = mapped_column(String(128))
    mandatory: Mapped[bool | None] = mapped_column(comment="NULL if the line does not say.")
    named_make: Mapped[str | None] = mapped_column(Text)
    single_firm: Mapped[bool] = mapped_column(default=False)
    acceptable_makes: Mapped[list[str]] = mapped_column(default=list)
    or_equivalent: Mapped[bool] = mapped_column(default=False)
    flags: Mapped[list[str]] = mapped_column(default=list)
    kind: Mapped[ValueKind] = mapped_column(str_enum(ValueKind))
    attribute: Mapped[str | None] = mapped_column(String(128))
    operator: Mapped[Operator | None] = mapped_column(str_enum(Operator))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_num: Mapped[float | None] = mapped_column(Float)
    value_min: Mapped[float | None] = mapped_column(Float)
    value_max: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(Text)
    canonical_unit: Mapped[str | None] = mapped_column(String(64))
    alternatives: Mapped[list[str]] = mapped_column(default=list)
    any_alternative: Mapped[bool] = mapped_column(default=False)
    weight: Mapped[float] = mapped_column(Float)

    run: Mapped[Run] = relationship(back_populates="requirements")
    line_item: Mapped[LineItem | None] = relationship(back_populates="requirements")
    parent: Mapped[Requirement | None] = relationship(
        remote_side="Requirement.id", back_populates="children"
    )
    children: Mapped[list[Requirement]] = relationship(
        back_populates="parent", order_by="Requirement.position"
    )
