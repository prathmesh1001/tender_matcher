"""Structured specification facts extracted from manufacturer documents."""

import uuid

from sqlalchemy import Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SpecFact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One verified attribute/value claim, e.g. `body_material = SS316`, with its source quote.

    Only facts whose `quote` was verified on `page_number` are stored.
    """

    __tablename__ = "spec_facts"
    __table_args__ = (Index("ix_spec_facts_lookup", "manufacturer", "category", "attribute"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"))
    page_number: Mapped[int]
    manufacturer: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    product_family: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    attribute: Mapped[str] = mapped_column(String(128), comment="Canonical ontology attribute.")
    value_text: Mapped[str] = mapped_column(Text, comment="Value as written in the source.")
    value_num: Mapped[float | None] = mapped_column(Float, comment="In canonical unit.")
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32), comment="Canonical unit.")
    quote: Mapped[str] = mapped_column(Text)
    quote_match_score: Mapped[float] = mapped_column(Float)
    ocr: Mapped[bool] = mapped_column(default=False)
    conflict: Mapped[bool] = mapped_column(default=False)
