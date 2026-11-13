"""Source files kept in Postgres (FILE_STORE_BACKEND=postgres): content by sha256, names by ref."""

from datetime import datetime

from sqlalchemy import ForeignKey, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin

SHA256_HEX_LENGTH = 64


class FileBlob(TimestampMixin, Base):
    """One file's bytes, stored once however many references name it."""

    __tablename__ = "file_blobs"

    sha256: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), primary_key=True)
    size: Mapped[int] = mapped_column(comment="Bytes.")
    content: Mapped[bytes] = mapped_column(LargeBinary)


class StoredFile(TimestampMixin, Base):
    """A store reference ("tenders/GEM_2026_B_8119613/bid.pdf") and the content it names."""

    __tablename__ = "stored_files"

    ref: Mapped[str] = mapped_column(Text, primary_key=True, comment="FileStore reference.")
    sha256: Mapped[str] = mapped_column(
        ForeignKey("file_blobs.sha256", ondelete="RESTRICT"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
