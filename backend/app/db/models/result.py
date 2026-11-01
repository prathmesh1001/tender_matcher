"""Per requirement x manufacturer match results and the evidence backing them."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import LabelBasis

if TYPE_CHECKING:
    from app.db.models.run import Run


class MatchResult(UUIDPrimaryKeyMixin, Base):
    """Decision for one requirement against one manufacturer's selected product model.

    Catalogues nothing tells apart give one row each, marked `tied`; none is picked. A general
    requirement is outside any product, so it has one row with no manufacturer.
    """

    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "requirement_id",
            "manufacturer",
            "catalogue",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_results_run_manufacturer", "run_id", "manufacturer"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    catalogue: Mapped[str | None] = mapped_column(Text, comment="Catalogue file this row is from.")
    tied: Mapped[bool] = mapped_column(default=False)
    product_family: Mapped[str | None] = mapped_column(String(256))
    model: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[MatchStatus] = mapped_column(str_enum(MatchStatus))
    score: Mapped[float | None] = mapped_column(Float, comment="NULL = excluded from overall %.")
    capability: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, comment="NULL = not computed yet.")
    needs_review: Mapped[bool] = mapped_column(default=False)
    decided_by: Mapped[DecisionSource] = mapped_column(str_enum(DecisionSource))
    basis: Mapped[LabelBasis] = mapped_column(str_enum(LabelBasis))
    reasoning: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="results")
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class Evidence(UUIDPrimaryKeyMixin, Base):
    """A cited quote on a specific document page supporting a `MatchResult`."""

    __tablename__ = "evidence"

    result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("results.id", ondelete="CASCADE"), index=True
    )
    # RESTRICT: a document that is cited by a stored result must not silently disappear.
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), index=True
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"))
    page_number: Mapped[int]
    label: Mapped[str] = mapped_column(Text, comment="The field's label as read.")
    value: Mapped[str] = mapped_column(Text, comment="The field's value as printed.")
    quote: Mapped[str] = mapped_column(Text)
    quote_parts: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[MatchStatus | None] = mapped_column(
        str_enum(MatchStatus), comment="What this field alone decides; NULL if nothing."
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool | None] = mapped_column(comment="NULL = not checked yet.")
    match_score: Mapped[float | None] = mapped_column(Float)

    result: Mapped[MatchResult] = relationship(back_populates="evidence")
