"""GeM Marketplace checks: one per run and uncovered product type."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.schemas.enums import DiscoveryStatus


class DiscoveryJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reading GeM listings for one product type of a run, and the run it produced."""

    __tablename__ = "discovery_jobs"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    product_type: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    status: Mapped[DiscoveryStatus] = mapped_column(str_enum(DiscoveryStatus), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    listings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, comment="Each listing read: url, brand, model, kept or why not."
    )
    result_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"),
        comment="The re-matched run with the kept listings.",
    )
    finished_at: Mapped[datetime | None]
