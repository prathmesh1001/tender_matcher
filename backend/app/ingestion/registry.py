"""Manufacturer source registry: load and validate every `sources.yaml` under the data root.

Layout::

    data/manufacturers/<manufacturer>/sources.yaml
    data/manufacturers/<manufacturer>/<file>.pdf

A document that is not registered here is never ingested, so every fact can be traced back to
a URL and download date.
"""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from app.core.exceptions import SourceRegistryError
from app.schemas.sources import RegisteredSource, SourceEntry, SourcesFile

SOURCES_FILENAME: Final = "sources.yaml"
_HASH_CHUNK_BYTES: Final = 1024 * 1024


def compute_sha256(path: Path) -> str:
    """Return the lower-case hex SHA-256 of a file, reading it in chunks.

    Args:
        path: File to hash.

    Returns:
        64-character hex digest.

    Raises:
        SourceRegistryError: If the file cannot be read.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise SourceRegistryError(
            f"Cannot read {path}: {exc}", details={"path": str(path)}
        ) from exc
    return digest.hexdigest()


def discover_sources_files(root: Path) -> list[Path]:
    """Find `<root>/<manufacturer>/sources.yaml` files, sorted for deterministic order.

    Args:
        root: The manufacturer data root.

    Returns:
        Paths of all registry files found.

    Raises:
        SourceRegistryError: If `root` is not a directory.
    """
    if not root.is_dir():
        raise SourceRegistryError(
            f"Manufacturer data dir not found: {root}", details={"path": str(root)}
        )
    return sorted(root.glob(f"*/{SOURCES_FILENAME}"))


def parse_sources_file(path: Path) -> SourcesFile:
    """Read and validate one `sources.yaml`.

    Args:
        path: The registry file.

    Returns:
        The validated file contents.

    Raises:
        SourceRegistryError: If the file is unreadable, not YAML, or fails validation.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SourceRegistryError(
            f"Cannot read {path}: {exc}", details={"path": str(path)}
        ) from exc
    try:
        return SourcesFile.model_validate(raw)
    except ValidationError as exc:
        raise SourceRegistryError(
            f"Invalid {path}: {exc.error_count()} error(s)",
            details={"path": str(path), "errors": _summarize_errors(exc)},
        ) from exc


def _summarize_errors(exc: ValidationError) -> list[str]:
    """Render pydantic errors as `location: message` strings for logs and API responses."""
    return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]


def _to_registered(entry: SourceEntry, manufacturer: str, registry_file: Path) -> RegisteredSource:
    """Attach manufacturer and resolved path to an entry."""
    return RegisteredSource(
        manufacturer=manufacturer,
        category=entry.category,
        product_types=entry.product_types,
        sub_types=entry.sub_types,
        title=entry.title,
        doc_type=entry.doc_type,
        url=entry.url,
        fetched_at=entry.fetched_at,
        publish_date=entry.publish_date,
        sha256=entry.sha256,
        path=(registry_file.parent / entry.file).resolve(),
        registry_file=registry_file,
    )


def _check_no_duplicates(sources: Iterable[RegisteredSource]) -> None:
    """Reject the same PDF (by hash) or the same path being registered twice."""
    seen_hash: dict[str, Path] = {}
    seen_path: set[Path] = set()
    for src in sources:
        if src.path in seen_path:
            raise SourceRegistryError(
                f"{src.path} is registered more than once", details={"path": str(src.path)}
            )
        if src.sha256 in seen_hash:
            raise SourceRegistryError(
                f"{src.path} has the same sha256 as {seen_hash[src.sha256]}",
                details={"sha256": src.sha256},
            )
        seen_path.add(src.path)
        seen_hash[src.sha256] = src.path


def load_registry(root: Path, *, category: str | None = None) -> list[RegisteredSource]:
    """Load every registered document under `root`.

    Duplicates are checked across the whole registry, before the category filter.

    Args:
        root: The manufacturer data root.
        category: If given, return only documents of this category.

    Returns:
        Registered sources ordered by registry file, then file order.

    Raises:
        SourceRegistryError: On any invalid file, or duplicate hash/path.
    """
    sources: list[RegisteredSource] = []
    for registry_file in discover_sources_files(root):
        parsed = parse_sources_file(registry_file)
        sources.extend(
            _to_registered(entry, parsed.manufacturer, registry_file) for entry in parsed.documents
        )
    _check_no_duplicates(sources)
    return [s for s in sources if category is None or s.category == category]


def verify_source_file(source: RegisteredSource) -> None:
    """Check that the registered PDF exists and its content matches the recorded hash.

    A mismatch means the file changed since it was registered (e.g. a newer catalogue was
    downloaded over it), so its provenance can no longer be trusted.

    Args:
        source: A registered document.

    Raises:
        SourceRegistryError: If the file is missing or its hash differs.
    """
    if not source.path.is_file():
        raise SourceRegistryError(
            f"Registered file is missing: {source.path}", details={"path": str(source.path)}
        )
    actual = compute_sha256(source.path)
    if actual != source.sha256:
        raise SourceRegistryError(
            f"sha256 mismatch for {source.path}",
            details={"path": str(source.path), "expected": source.sha256, "actual": actual},
        )
