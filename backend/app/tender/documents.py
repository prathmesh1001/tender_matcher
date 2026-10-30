"""Tender document ingestion: bid.pdf and every linked file -> pages + chunks in Postgres.

A tender folder holds `tender.yaml` (provenance) plus the stored files. Each stored file is
checked against its recorded sha256, processed with the same stages as catalogues (so tender
text and catalogue text are chunked and normalized identically), stored once as a `Document`
(reused if another tender links the same file) and linked to the tender with its role, URL,
the file it was found in and its link depth.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sqlalchemy.orm import Session

from app.core.exceptions import TenderParseError
from app.core.logging import log_context
from app.db.models import Document, Tender, TenderDocument
from app.db.repositories import DocumentRepository, TenderRepository
from app.ingestion.pipeline import (
    IngestOutcome,
    IngestStatus,
    ReprocessPolicy,
    attach_content,
    rebuild_sections,
    replace_content,
)
from app.ingestion.processing import (
    ProcessingOptions,
    is_supported,
    process_file,
    tag_products,
)
from app.ingestion.product_tagger import Declared
from app.schemas.enums import DocumentSource
from app.schemas.tender_files import ManifestFile, TenderManifest
from app.storage.file_store import FileStore
from app.tender.link_fetcher import BID_ROLE, MANIFEST_FILENAME, read_manifest

logger = logging.getLogger(__name__)

_HASH_CHUNK: Final = 1024 * 1024


@dataclass(slots=True)
class TenderIngestOutcome:
    """Result of ingesting one tender folder."""

    bid_number: str
    tender_id: uuid.UUID
    created: bool
    files: list[IngestOutcome] = field(default_factory=list)


def _file_sha256(path: Path) -> str:
    """Hex SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _bid_entry(manifest: TenderManifest) -> ManifestFile:
    """The manifest entry of bid.pdf.

    Raises:
        TenderParseError: If the manifest has no stored bid document.
    """
    for entry in manifest.stored_files():
        if entry.role == BID_ROLE:
            return entry
    raise TenderParseError(
        f"{manifest.bid_number}: tender.yaml has no stored {BID_ROLE}",
        details={"bid_number": manifest.bid_number},
    )


def _verified_sha256(store: FileStore, ref: str, entry: ManifestFile) -> str:
    """Hash a stored file and check it against the manifest.

    Raises:
        TenderParseError: If the recorded and actual sha256 differ.
    """
    with store.local_path(ref) as path:
        actual = _file_sha256(path)
    if entry.sha256 is not None and entry.sha256 != actual:
        raise TenderParseError(
            f"sha256 mismatch for {ref}: tender.yaml says {entry.sha256}, file is {actual}",
            details={"ref": ref, "expected": entry.sha256, "actual": actual},
        )
    return actual


def _get_or_create_tender(
    session: Session, store: FileStore, tender_ref: str, manifest: TenderManifest
) -> tuple[Tender, bool]:
    """Find the tender by its bid.pdf hash, or create it."""
    bid = _bid_entry(manifest)
    bid_ref = f"{tender_ref}/{bid.file}"
    sha = _verified_sha256(store, bid_ref, bid)
    repo = TenderRepository(session)
    existing = repo.get_by_sha256(sha)
    if existing is not None:
        return existing, False
    tender = Tender(
        filename=bid.file or "",
        file_path=bid_ref,
        sha256=sha,
        title=manifest.item_category,
        bid_number=manifest.bid_number,
        gem_bid_id=None if manifest.gem_bid_id is None else str(manifest.gem_bid_id),
    )
    return repo.add(tender), True


def _tender_document(entry: ManifestFile, ref: str, sha: str, title: str) -> Document:
    """Unsaved document row for a tender file."""
    return Document(
        source_kind=DocumentSource.TENDER,
        title=title,
        url=entry.url,
        file_path=ref,
        sha256=sha,
        fetched_at=entry.fetched_at or datetime.now(UTC),
    )


def _ingest_file(
    session: Session,
    store: FileStore,
    tender: Tender,
    ref: str,
    entry: ManifestFile,
    options: ProcessingOptions,
    reprocess: ReprocessPolicy,
) -> IngestOutcome:
    """Store one tender file (once, by sha256) and link it to the tender."""
    if not is_supported(Path(ref)):
        logger.info("tender file type not parsed", extra={"file": ref, "role": entry.role})
        return IngestOutcome(ref, IngestStatus.SKIPPED_UNSUPPORTED)
    sha = _verified_sha256(store, ref, entry)
    repo = DocumentRepository(session)
    document = repo.get_by_sha256(sha)
    if document is not None and not reprocess.claim(sha):
        _link(session, tender, document, entry)
        return IngestOutcome(ref, IngestStatus.SKIPPED_EXISTING, document.id)
    with store.local_path(ref) as path:
        # GeM bid documents are label/value tables; no material-version split (see for_tender).
        processed = process_file(path, options.for_tender())
    processed = tag_products(processed, options, Declared())
    if document is not None:
        replace_content(session, document, processed)
        status = IngestStatus.REPROCESSED
    else:
        document = _tender_document(entry, ref, sha, f"{tender.bid_number} {entry.role}")
        attach_content(document, processed)
        repo.add(document)
        status = IngestStatus.INGESTED
    rebuild_sections(session, document, options)
    _link(session, tender, document, entry)
    return IngestOutcome(
        ref, status, document.id, pages=len(processed.pages), chunks=len(processed.chunks)
    )


def _link(session: Session, tender: Tender, document: Document, entry: ManifestFile) -> None:
    """Link a document to a tender unless it already is."""
    repo = TenderRepository(session)
    if document.id in repo.linked_document_ids(tender.id):
        return
    repo.add_document_link(
        TenderDocument(
            tender_id=tender.id,
            document_id=document.id,
            role=entry.role,
            url=entry.url,
            found_in=entry.found_in,
            depth=entry.depth,
        )
    )
    if entry.role == BID_ROLE:
        tender.page_count = document.page_count


def ingest_tender_folder(
    session: Session,
    store: FileStore,
    tender_dir: Path,
    options: ProcessingOptions,
    reprocess: ReprocessPolicy | None = None,
) -> TenderIngestOutcome:
    """Ingest every stored file of one tender folder.

    Args:
        session: Open DB session (the caller commits).
        store: File store holding the tender folder.
        tender_dir: Folder with `tender.yaml`.
        options: Processing options.
        reprocess: If enabled, already stored files are re-parsed in place (once per run).

    Returns:
        The tender and what happened to each file.

    Raises:
        TenderParseError: If the manifest is invalid, has no bid.pdf, or a hash differs.
        DocumentParseError: If a file cannot be parsed.
    """
    policy = reprocess or ReprocessPolicy()
    manifest = read_manifest(tender_dir / MANIFEST_FILENAME)
    tender_ref = store.ref_for(tender_dir)
    tender, created = _get_or_create_tender(session, store, tender_ref, manifest)
    outcome = TenderIngestOutcome(manifest.bid_number, tender.id, created)
    with log_context(tender_id=str(tender.id)):
        for entry in manifest.stored_files():
            ref = f"{tender_ref}/{entry.file}"
            outcome.files.append(_ingest_file(session, store, tender, ref, entry, options, policy))
    logger.info(
        "tender ingested",
        extra={"bid_number": manifest.bid_number, "files": len(outcome.files)},
    )
    return outcome
