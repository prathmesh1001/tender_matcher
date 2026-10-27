"""Uploaded tenders and their atomic requirements."""

import uuid

from sqlalchemy import Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import Operator, RequirementType


class Tender(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tender PDF. Unique by content hash so re-uploads reuse the parsed requirements."""

    __tablename__ = "tenders"

    filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    bid_number: Mapped[str | None] = mapped_column(String(128))
    page_count: Mapped[int] = mapped_column(default=0)

    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="Requirement.position"
    )


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
