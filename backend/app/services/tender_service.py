"""Use case: store an uploaded bid.pdf (or a ZIP of it and its attachments) as a tender.

Upload only stores the files, so it answers quickly. A ZIP's other files (BOQ, spec, ATC) are
added to the folder and `tender.yaml` with their roles, the same way linked files are. Linked
files are fetched and read when a run starts. It commits before returning, so a run started
right after always finds the tender. A different bid.pdf for a known bid number becomes a new
revision in its own folder: stored files are never overwritten, because past runs cite them.
"""

import logging
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DocumentParseError, InvalidInputError, TenderParseError
from app.db.models import Tender
from app.db.repositories import TenderRepository
from app.db.session import session_scope
from app.ingestion.pdf_parser import open_pdf
from app.schemas.tender_files import ManifestFile
from app.schemas.tenders import TenderUploadResponse
from app.storage.file_store import FileStore
from app.tender.link_fetcher import (
    BID_FILENAME,
    MANIFEST_FILENAME,
    bid_number_from_pdf,
    init_manifest,
    read_manifest,
    sha256_hex,
    tender_folder,
    unique_name,
    write_manifest,
)
from app.tender.zip_upload import (
    CompiledZipRules,
    ZipLimits,
    ZipMember,
    is_zip_upload,
    unpack_tender,
)

logger = logging.getLogger(__name__)

_PDF_MAGIC: Final = b"%PDF"
_BYTES_PER_MB: Final = 1024 * 1024


class TenderService:
    """Store uploaded tenders."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: FileStore,
        tender_root: Path,
        max_upload_mb: int,
        zip_rules: CompiledZipRules,
        zip_limits: ZipLimits,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._tender_root = tender_root
        self._max_bytes = max_upload_mb * _BYTES_PER_MB
        self._zip_rules = zip_rules
        self._zip_limits = zip_limits

    def upload(self, filename: str, content: bytes) -> TenderUploadResponse:
        """Store one bid.pdf, or a ZIP holding it and its attachments.

        Uploading the same bid.pdf again (alone or in a ZIP) reuses its tender; a ZIP's
        attachments not stored yet are added to it.

        Args:
            filename: The name it was uploaded under (kept in the response and the manifest).
            content: The file.

        Returns:
            The tender, its page count, bid number and revision, whether it is new, and the
            attachments stored and skipped.

        Raises:
            InvalidInputError: If it is neither a PDF nor a usable ZIP, or is too large.
            TenderParseError: If no GeM bid number is printed on page 1 (of any PDF in a ZIP).
            DocumentParseError: If the PDF cannot be opened.
        """
        self._check(content)
        attachments: list[tuple[ZipMember, str]] = []
        skipped: list[str] = []
        if is_zip_upload(content):
            unpacked = unpack_tender(
                content, self._zip_rules, self._zip_limits, _bid_number_or_none
            )
            content, attachments, skipped = (
                unpacked.bid.content,
                unpacked.attachments,
                unpacked.skipped,
            )
        elif not content.startswith(_PDF_MAGIC):
            raise InvalidInputError("The uploaded file is neither a PDF nor a ZIP")
        bid_number, pages = _read_bid(content)
        sha256 = sha256_hex(content)
        with session_scope(self._session_factory) as session:
            repo = TenderRepository(session)
            existing = repo.get_by_sha256(sha256)
            revision = (
                existing.revision
                if existing
                else self._free_revision(bid_number, sha256, repo.latest_revision(bid_number) + 1)
            )
            folder = tender_folder(bid_number, revision)
            ref = self._store_files(folder, content)
            stored = self._store_attachments(folder, attachments, filename)
            tender = existing or repo.add(
                Tender(
                    filename=BID_FILENAME,
                    file_path=ref,
                    sha256=sha256,
                    bid_number=bid_number,
                    revision=revision,
                    page_count=pages,
                )
            )
            response = TenderUploadResponse(
                tender_id=tender.id,
                filename=filename,
                pages=pages,
                bid_number=bid_number,
                revision=tender.revision,
                created=existing is None,
                attachments=stored,
                skipped=skipped,
            )
        logger.info("tender stored", extra={"tender_id": str(response.tender_id)})
        return response

    def _check(self, content: bytes) -> None:
        """Refuse an upload that is too large before reading it."""
        if len(content) > self._max_bytes:
            raise InvalidInputError(
                f"The uploaded file is larger than {self._max_bytes // _BYTES_PER_MB} MB",
                details={"bytes": len(content)},
            )

    def _free_revision(self, bid_number: str, sha256: str, first: int) -> int:
        """The first revision from `first` whose folder has no other bid.pdf (never overwrite)."""
        revision = first
        while True:
            bid = self._tender_root / tender_folder(bid_number, revision) / BID_FILENAME
            if not bid.is_file() or sha256_hex(bid.read_bytes()) == sha256:
                return revision
            revision += 1

    def _store_files(self, folder: str, content: bytes) -> str:
        """Write bid.pdf and tender.yaml unless the folder has them; return bid.pdf's reference."""
        tender_dir = self._tender_root / folder
        bid = tender_dir / BID_FILENAME
        ref = self._store.ref_for(bid)
        if not bid.is_file():
            self._store.write(ref, content)
        manifest = tender_dir / MANIFEST_FILENAME
        if not manifest.is_file():
            write_manifest(manifest, init_manifest(tender_dir))
        return ref

    def _store_attachments(
        self, folder: str, attachments: Sequence[tuple[ZipMember, str]], upload_name: str
    ) -> list[str]:
        """Add a ZIP's other files to the folder and manifest; files already there are kept.

        Returns:
            The stored file names (a file whose sha256 is already stored is not added again).
        """
        if not attachments:
            return []
        tender_dir = self._tender_root / folder
        manifest_path = tender_dir / MANIFEST_FILENAME
        manifest = read_manifest(manifest_path)
        known = {f.sha256 for f in manifest.files if f.sha256}
        taken = {f.file for f in manifest.files if f.file} | {p.name for p in tender_dir.iterdir()}
        stored: list[str] = []
        for member, role in attachments:
            sha256 = sha256_hex(member.content)
            if sha256 in known:
                continue
            name = unique_name(role, Path(member.name).suffix.lower(), taken)
            self._store.write(self._store.ref_for(tender_dir / name), member.content)
            manifest.files.append(_zip_entry(name, role, sha256, member, upload_name))
            known.add(sha256)
            taken.add(name)
            stored.append(name)
        write_manifest(manifest_path, manifest)
        logger.info("zip attachments stored", extra={"tender": folder, "files": stored})
        return stored


def _zip_entry(
    name: str, role: str, sha256: str, member: ZipMember, upload_name: str
) -> ManifestFile:
    """The manifest entry of one file taken from an uploaded ZIP (no source URL)."""
    return ManifestFile(
        file=name,
        role=role,
        url=None,
        fetched_at=datetime.now(UTC),
        sha256=sha256,
        depth=1,
        note=f"from uploaded ZIP {upload_name}".strip(),
        original_name=member.path,
    )


def _bid_number_or_none(content: bytes) -> str | None:
    """The GeM bid number on page 1 of a PDF's bytes, or None (no number, or not readable)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / BID_FILENAME
        path.write_bytes(content)
        try:
            return bid_number_from_pdf(path)
        except TenderParseError, DocumentParseError:
            return None


def _read_bid(content: bytes) -> tuple[str, int]:
    """The bid number and page count of an uploaded file, read before it is stored anywhere."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / BID_FILENAME
        path.write_bytes(content)
        bid_number = bid_number_from_pdf(path)
        with open_pdf(path) as doc:
            return bid_number, doc.page_count
