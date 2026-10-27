"""Matching runs: one tender evaluated against a set of manufacturers."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import RunStatus

if TYPE_CHECKING:
    from app.db.models.result import MatchResult
    from app.db.models.tender import Tender


class Run(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A queued/executed matching job; stored so past runs can be listed and re-opened."""

    __tablename__ = "runs"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(64))
    manufacturers: Mapped[list[str]]
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    tender: Mapped[Tender] = relationship()
    results: Mapped[list[MatchResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
