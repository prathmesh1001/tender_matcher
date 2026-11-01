"""Use case: store an uploaded bid.pdf as a tender folder and a tender row.

Upload only stores the file, so it answers quickly. Linked files are fetched and read when a run
starts. It commits before returning, so a run started right after always finds the tender.
"""

import logging
import tempfile
from pathlib import Path
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import InvalidInputError
from app.db.models import Tender
from app.db.repositories import TenderRepository
from app.db.session import session_scope
from app.ingestion.pdf_parser import open_pdf
from app.schemas.tenders import TenderUploadResponse
from app.storage.file_store import FileStore
from app.tender.link_fetcher import (
    BID_FILENAME,
    MANIFEST_FILENAME,
    bid_number_from_pdf,
    init_manifest,
    sha256_hex,
    tender_folder,
    write_manifest,
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
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._tender_root = tender_root
        self._max_bytes = max_upload_mb * _BYTES_PER_MB

    def upload(self, filename: str, content: bytes) -> TenderUploadResponse:
        """Store one bid.pdf; uploading the same file again reuses its tender.

        Args:
            filename: The name it was uploaded under (kept only in the response).
            content: The file.

        Returns:
            The tender, its page count and bid number, and whether it is new.

        Raises:
            InvalidInputError: If it is not a PDF, is too large, or another bid.pdf is already
                stored for this bid number: it is never overwritten, past runs cite it.
            TenderParseError: If no GeM bid number is printed on page 1.
            DocumentParseError: If the PDF cannot be opened.
        """
        self._check(content)
        bid_number, pages = _read_bid(content)
        folder = tender_folder(bid_number)
        sha256 = sha256_hex(content)
        with session_scope(self._session_factory) as session:
            repo = TenderRepository(session)
            existing = repo.get_by_sha256(sha256)
            other = repo.get_by_bid_number(bid_number)
            if existing is None and other is not None:
                raise _different_bid(bid_number, sha256, other.sha256)
            ref = self._store_files(folder, bid_number, sha256, content)
            tender = existing or repo.add(
                Tender(
                    filename=BID_FILENAME,
                    file_path=ref,
                    sha256=sha256,
                    bid_number=bid_number,
                    page_count=pages,
                )
            )
            response = TenderUploadResponse(
                tender_id=tender.id,
                filename=filename,
                pages=pages,
                bid_number=bid_number,
                created=existing is None,
            )
        logger.info("tender stored", extra={"tender_id": str(response.tender_id)})
        return response

    def _check(self, content: bytes) -> None:
        """Refuse what cannot be a bid.pdf before reading it."""
        if not content.startswith(_PDF_MAGIC):
            raise InvalidInputError("The uploaded file is not a PDF")
        if len(content) > self._max_bytes:
            raise InvalidInputError(
                f"The uploaded file is larger than {self._max_bytes // _BYTES_PER_MB} MB",
                details={"bytes": len(content)},
            )

    def _store_files(self, folder: str, bid_number: str, sha256: str, content: bytes) -> str:
        """Write bid.pdf and tender.yaml unless the folder has them; return bid.pdf's reference."""
        tender_dir = self._tender_root / folder
        bid = tender_dir / BID_FILENAME
        if bid.is_file() and sha256_hex(bid.read_bytes()) != sha256:
            raise _different_bid(bid_number, sha256, sha256_hex(bid.read_bytes()))
        ref = self._store.ref_for(bid)
        if not bid.is_file():
            self._store.write(ref, content)
        manifest = tender_dir / MANIFEST_FILENAME
        if not manifest.is_file():
            write_manifest(manifest, init_manifest(tender_dir))
        return ref


def _read_bid(content: bytes) -> tuple[str, int]:
    """The bid number and page count of an uploaded file, read before it is stored anywhere."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / BID_FILENAME
        path.write_bytes(content)
        bid_number = bid_number_from_pdf(path)
        with open_pdf(path) as doc:
            return bid_number, doc.page_count


def _different_bid(bid_number: str, uploaded: str, stored: str) -> InvalidInputError:
    return InvalidInputError(
        f"A different bid.pdf is already stored for {bid_number}",
        details={"bid_number": bid_number, "uploaded_sha256": uploaded, "stored_sha256": stored},
    )
