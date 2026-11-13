"""Queries for files kept in Postgres: blobs by sha256 and references to them."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.stored_file import FileBlob, StoredFile


class StoredFileRepository:
    """Read and write stored files; flushes, never commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def content(self, ref: str) -> bytes | None:
        """The bytes stored under `ref`, or None if nothing is."""
        statement = (
            select(FileBlob.content)
            .join(StoredFile, StoredFile.sha256 == FileBlob.sha256)
            .where(StoredFile.ref == ref)
        )
        return self._session.scalars(statement).first()

    def sha256_of(self, ref: str) -> str | None:
        """The content hash stored under `ref`, or None if nothing is."""
        found = self._session.get(StoredFile, ref)
        return found.sha256 if found else None

    def exists(self, ref: str) -> bool:
        """True if a file is stored under `ref`."""
        return self._session.get(StoredFile, ref) is not None

    def put(self, ref: str, sha256: str, content: bytes) -> bool:
        """Store `content` under `ref`; the blob is written only if its hash is new.

        Args:
            ref: Store reference.
            sha256: Hash of `content` (hex).
            content: The file's bytes.

        Returns:
            True if the blob was new (False: the same content was already stored).
        """
        added = self._session.execute(
            insert(FileBlob)
            .values(sha256=sha256, size=len(content), content=content)
            .on_conflict_do_nothing(index_elements=[FileBlob.sha256])
            .returning(FileBlob.sha256)
        ).first()
        self._session.execute(
            insert(StoredFile)
            .values(ref=ref, sha256=sha256)
            .on_conflict_do_update(index_elements=[StoredFile.ref], set_={"sha256": sha256})
        )
        self._session.flush()
        return added is not None

    def count(self) -> tuple[int, int]:
        """How many references and how many distinct blobs are stored."""
        refs = self._session.scalar(select(func.count()).select_from(StoredFile)) or 0
        blobs = self._session.scalar(select(func.count()).select_from(FileBlob)) or 0
        return refs, blobs
