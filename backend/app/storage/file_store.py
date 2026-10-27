"""Where source files (catalogues, tender PDFs and their attachments) are kept.

Pipelines never open `data/` paths directly; they ask a `FileStore` for a local path by a
store reference (a POSIX path relative to the store root, e.g.
`tenders/GEM_2026_B_8119613/bid.pdf`). Today files live on the dev machine
(`LocalFileStore`); a Postgres-backed store keyed by sha256 replaces it later (plan step 16)
without changing any pipeline.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.config import FileStoreBackend, Settings
from app.core.exceptions import InvalidInputError, NotFoundError


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
        relative = PurePosixPath(ref)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise InvalidInputError(f"Invalid file reference: {ref!r}", details={"ref": ref})
        return self._root.joinpath(*relative.parts)

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
        try:
            return path.resolve().relative_to(self._root).as_posix()
        except ValueError as exc:
            raise InvalidInputError(
                f"{path} is outside the file store root {self._root}", details={"path": str(path)}
            ) from exc


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
