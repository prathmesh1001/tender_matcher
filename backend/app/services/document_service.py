"""Use case: hand out a stored document, so evidence can open at its cited page."""

import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from app.core.exceptions import NotFoundError
from app.db.repositories import DocumentRepository
from app.storage.file_store import FileStore

_DEFAULT_MEDIA_TYPE: Final = "application/octet-stream"
# Saved third-party pages (GeM listing snapshots) must never run scripts on our origin.
_SANDBOXED_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})


@dataclass(frozen=True, slots=True)
class StoredFile:
    """A stored document's bytes and how to serve them."""

    filename: str
    media_type: str
    content: bytes

    @property
    def sandboxed(self) -> bool:
        """True for a saved web page: served under a CSP sandbox (no scripts, no origin)."""
        return self.media_type in _SANDBOXED_TYPES


class DocumentService:
    """Read stored documents by content hash."""

    def __init__(self, documents: DocumentRepository, store: FileStore) -> None:
        self._documents = documents
        self._store = store

    def get_file(self, sha256: str) -> StoredFile:
        """Return one stored document.

        Args:
            sha256: The document's content hash.

        Returns:
            Its file name, media type and bytes.

        Raises:
            NotFoundError: If no document has this hash, or its file is gone.
        """
        document = self._documents.get_by_sha256(sha256)
        if document is None or not self._store.exists(document.file_path):
            raise NotFoundError(f"Document {sha256} not found", details={"sha256": sha256})
        with self._store.local_path(document.file_path) as path:
            content = path.read_bytes()
        filename = PurePosixPath(document.file_path).name
        media_type = mimetypes.guess_type(filename)[0] or _DEFAULT_MEDIA_TYPE
        return StoredFile(filename, media_type, content)
