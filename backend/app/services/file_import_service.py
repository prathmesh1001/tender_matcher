"""Use case: copy the local source files into Postgres, once, before FILE_STORE_BACKEND=postgres.

Every file under the manufacturer and tender data folders is copied under the same reference
it has locally, so stored documents keep their `file_path`. Provenance YAML stays on disk.
Re-running copies nothing that is already stored with the same content.
"""

import hashlib
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.repositories.stored_file_repository import StoredFileRepository
from app.db.session import create_db_engine, create_session_factory, session_scope
from app.storage.file_store import ref_under

logger = logging.getLogger(__name__)

# Provenance files are read from disk whatever the store (tracked in git).
_KEPT_ON_DISK = frozenset({".yaml", ".yml"})


@dataclass(slots=True)
class ImportResult:
    """What one import did."""

    copied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    new_blobs: int = 0


def source_files(folders: Sequence[Path]) -> Iterator[Path]:
    """Every source file under the folders, in path order; provenance YAML left out.

    Args:
        folders: Data folders (missing ones are skipped).

    Yields:
        File paths.
    """
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix.lower() not in _KEPT_ON_DISK:
                yield path


class FileImportService:
    """Copy local files into the Postgres file store."""

    def __init__(self, session_factory: sessionmaker[Session], root: Path) -> None:
        self._session_factory = session_factory
        self._root = root.resolve()

    def import_files(self, folders: Sequence[Path]) -> ImportResult:
        """Copy every source file under the folders, one transaction per file.

        Args:
            folders: Data folders under the store root.

        Returns:
            Which references were copied and which were already stored unchanged.
        """
        result = ImportResult()
        for path in source_files(folders):
            ref = ref_under(self._root, path)
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            with session_scope(self._session_factory) as session:
                files = StoredFileRepository(session)
                if files.sha256_of(ref) == digest:
                    result.unchanged.append(ref)
                    continue
                result.new_blobs += int(files.put(ref, digest, content))
            result.copied.append(ref)
            logger.info("file imported", extra={"ref": ref, "bytes": len(content)})
        return result


def build_file_import_service(settings: Settings) -> FileImportService:
    """Wire the import service to the configured database and store root."""
    return FileImportService(
        create_session_factory(create_db_engine(settings)), settings.FILE_STORE_ROOT
    )
