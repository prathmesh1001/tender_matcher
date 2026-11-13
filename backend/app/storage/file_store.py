"""Where source files (catalogues, tender PDFs and their attachments) are kept.

Pipelines ask a `FileStore` for a local path by a store reference (a POSIX path relative to
the store root, e.g. `tenders/GEM_2026_B_8119613/bid.pdf`). `LocalFileStore` keeps files on
disk under FILE_STORE_ROOT; `PostgresFileStore` keeps them in Postgres, content once per sha256,
and writes a temporary copy when a parser needs a real path. The provenance YAML files
(`sources.yaml`, `tender.yaml`) stay on disk either way: they are small and tracked in git.
"""

import hashlib
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path, PurePosixPath
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import FileStoreBackend, Settings
from app.core.exceptions import InvalidInputError, NotFoundError
from app.db.repositories.stored_file_repository import StoredFileRepository
from app.db.session import create_db_engine, create_session_factory, session_scope


class FileStore(Protocol):
    """Read and write source files by store reference."""

    def local_path(self, ref: str) -> AbstractContextManager[Path]:
        """Yield a readable local path for `ref` (temporary for non-disk stores)."""
        ...

    def exists(self, ref: str) -> bool:
        """True if a file is stored under `ref`."""
        ...

    def write(self, ref: str, content: bytes) -> None:
        """Store `content` under `ref`, replacing any existing file."""
        ...

    def ref_for(self, path: Path) -> str:
        """Return the store reference of a file that lives inside the store."""
        ...


def checked_ref(ref: str) -> PurePosixPath:
    """A reference as a relative path, refusing absolute paths and "..".

    Raises:
        InvalidInputError: If the reference could point outside the store.
    """
    relative = PurePosixPath(ref)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise InvalidInputError(f"Invalid file reference: {ref!r}", details={"ref": ref})
    return relative


def ref_under(root: Path, path: Path) -> str:
    """The reference of a path inside `root`.

    Raises:
        InvalidInputError: If the path is outside the root.
    """
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise InvalidInputError(
            f"{path} is outside the file store root {root}", details={"path": str(path)}
        ) from exc


class LocalFileStore:
    """Files on the local disk under one root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        """The absolute root directory."""
        return self._root

    def _resolve(self, ref: str) -> Path:
        """Map a reference to an absolute path, refusing anything outside the root."""
        return self._root.joinpath(*checked_ref(ref).parts)

    @contextmanager
    def local_path(self, ref: str) -> Iterator[Path]:
        """Yield the file's real path.

        Raises:
            NotFoundError: If no file is stored under `ref`.
        """
        path = self._resolve(ref)
        if not path.is_file():
            raise NotFoundError(f"Stored file not found: {ref}", details={"ref": ref})
        yield path

    def exists(self, ref: str) -> bool:
        """True if a file is stored under `ref`."""
        return self._resolve(ref).is_file()

    def write(self, ref: str, content: bytes) -> None:
        """Write `content` under `ref`, creating parent folders."""
        path = self._resolve(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def ref_for(self, path: Path) -> str:
        """Return the reference of a path inside the root.

        Raises:
            InvalidInputError: If the path is outside the store root.
        """
        return ref_under(self._root, path)


class PostgresFileStore:
    """Files in Postgres: bytes once per sha256 (`file_blobs`), names in `stored_files`.

    References are the same as the local store's (relative to FILE_STORE_ROOT), so documents
    already ingested keep their `file_path`.
    """

    def __init__(self, session_factory: sessionmaker[Session], root: Path) -> None:
        self._session_factory = session_factory
        self._root = root.resolve()

    @contextmanager
    def local_path(self, ref: str) -> Iterator[Path]:
        """Yield a temporary copy of the file, named like the original (parsers read suffixes).

        Raises:
            NotFoundError: If no file is stored under `ref`.
        """
        name = checked_ref(ref).name
        with session_scope(self._session_factory) as session:
            content = StoredFileRepository(session).content(ref)
        if content is None:
            raise NotFoundError(f"Stored file not found: {ref}", details={"ref": ref})
        with tempfile.TemporaryDirectory(prefix="filestore-") as folder:
            path = Path(folder) / name
            path.write_bytes(content)
            yield path

    def exists(self, ref: str) -> bool:
        """True if a file is stored under `ref`."""
        checked_ref(ref)
        with session_scope(self._session_factory) as session:
            return StoredFileRepository(session).exists(ref)

    def write(self, ref: str, content: bytes) -> None:
        """Store `content` under `ref`, replacing what the reference named before."""
        checked_ref(ref)
        digest = hashlib.sha256(content).hexdigest()
        with session_scope(self._session_factory) as session:
            StoredFileRepository(session).put(ref, digest, content)

    def ref_for(self, path: Path) -> str:
        """Return the reference a path under FILE_STORE_ROOT has.

        Raises:
            InvalidInputError: If the path is outside FILE_STORE_ROOT.
        """
        return ref_under(self._root, path)


_FACTORIES: dict[str, sessionmaker[Session]] = {}


def _session_factory(settings: Settings) -> sessionmaker[Session]:
    """One engine per database for every Postgres store built in this process."""
    key = str(settings.DATABASE_URL)
    if key not in _FACTORIES:
        _FACTORIES[key] = create_session_factory(create_db_engine(settings))
    return _FACTORIES[key]


def build_file_store(settings: Settings) -> FileStore:
    """Create the file store selected by `FILE_STORE_BACKEND`.

    Args:
        settings: Application settings.

    Returns:
        The configured store.
    """
    match settings.FILE_STORE_BACKEND:
        case FileStoreBackend.LOCAL:
            return LocalFileStore(settings.FILE_STORE_ROOT)
        case FileStoreBackend.POSTGRES:
            return PostgresFileStore(_session_factory(settings), settings.FILE_STORE_ROOT)
