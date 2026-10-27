"""Per requirement x manufacturer match results and the evidence backing them."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import DecisionSource, MatchStatus

if TYPE_CHECKING:
    from app.db.models.run import Run


class MatchResult(UUIDPrimaryKeyMixin, Base):
    """Decision for one requirement against one manufacturer's selected product model."""

    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint("run_id", "requirement_id", "manufacturer"),
        Index("ix_results_run_manufacturer", "run_id", "manufacturer"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    manufacturer: Mapped[str] = mapped_column(String(128))
    product_family: Mapped[str | None] = mapped_column(String(256))
    model: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[MatchStatus] = mapped_column(str_enum(MatchStatus))
    score: Mapped[float | None] = mapped_column(Float, comment="NULL = excluded from overall %.")
    capability: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(default=False)
    decided_by: Mapped[DecisionSource] = mapped_column(str_enum(DecisionSource))
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
    quote: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False)
    match_score: Mapped[float] = mapped_column(Float)

    result: Mapped[MatchResult] = relationship(back_populates="evidence")
