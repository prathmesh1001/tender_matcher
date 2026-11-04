"""Uploaded tender ZIPs: finding the bid PDF, roles of the other files, unsafe archives.

Archives are built in memory; the "PDF" bytes are fakes because the bid number reader is
injected (the real reader is covered by the API test).
"""

import io
import zipfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import InvalidInputError, TenderParseError
from app.ingestion.processing import SUPPORTED_SUFFIXES
from app.tender.zip_upload import ZipLimits, is_zip_upload, load_zip_rules, unpack_tender

BACKEND = Path(__file__).resolve().parents[3]
SETTINGS = Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost/db")  # type: ignore[arg-type]
RULES = load_zip_rules(BACKEND / SETTINGS.TENDER_ZIP_RULES_FILE)
LIMITS = ZipLimits(max_files=10, max_unpacked_mb=1, readable_suffixes=SUPPORTED_SUFFIXES)
BID = b"%PDF bid GEM/2099/B/1234567"


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _bid_number(content: bytes) -> str | None:
    """Fake page-1 reader: the bid number written in the fake PDF bytes."""
    marker = b"GEM/"
    return content[content.index(marker) :].decode() if marker in content else None


def test_bid_pdf_is_found_and_attachments_get_roles() -> None:
    archive = _zip(
        {
            "tender/GeM-Bidding-1234567.pdf": BID,
            "tender/ATC_document.pdf": b"%PDF atc",
            "tender/BOQ Items.xlsx": b"sheet",
            "tender/Technical Specification.pdf": b"%PDF spec",
            "tender/Drawing 12.pdf": b"%PDF drawing",
        }
    )
    unpacked = unpack_tender(archive, RULES, LIMITS, _bid_number)
    assert unpacked.bid_number == "GEM/2099/B/1234567"
    assert unpacked.bid.name == "GeM-Bidding-1234567.pdf"
    roles = {m.name: role for m, role in unpacked.attachments}
    assert roles == {
        "ATC_document.pdf": "atc",
        "BOQ Items.xlsx": "boq_items",
        "Technical Specification.pdf": "buyer_spec",
        "Drawing 12.pdf": "attachment",
    }


def test_unreadable_types_nested_archives_and_zip_junk_are_skipped() -> None:
    archive = _zip(
        {
            "bid.pdf": BID,
            "photo.jpg": b"jpg",
            "inner.zip": _zip({"x.pdf": b"%PDF"}),
            "__MACOSX/._bid.pdf": b"junk",
        }
    )
    unpacked = unpack_tender(archive, RULES, LIMITS, _bid_number)
    assert unpacked.attachments == []
    assert unpacked.skipped == ["photo.jpg: file type not read", "inner.zip: file type not read"]


def test_folders_inside_the_archive_never_reach_the_file_name() -> None:
    archive = _zip({"../../evil/bid.pdf": BID, "../../evil/spec.pdf": b"%PDF spec"})
    unpacked = unpack_tender(archive, RULES, LIMITS, _bid_number)
    assert unpacked.bid.name == "bid.pdf"
    assert [m.name for m, _ in unpacked.attachments] == ["spec.pdf"]


def test_zip_without_a_bid_pdf_is_refused() -> None:
    with pytest.raises(TenderParseError, match="No PDF in the ZIP"):
        unpack_tender(_zip({"spec.pdf": b"%PDF spec"}), RULES, LIMITS, _bid_number)


def test_pdfs_of_different_bids_are_refused() -> None:
    archive = _zip({"a.pdf": BID, "b.pdf": b"%PDF GEM/2099/B/7654321"})
    with pytest.raises(TenderParseError, match="different bids"):
        unpack_tender(archive, RULES, LIMITS, _bid_number)


def test_too_many_files_are_refused_before_reading() -> None:
    archive = _zip({f"f{i}.pdf": b"%PDF" for i in range(11)})
    with pytest.raises(InvalidInputError, match="at most 10"):
        unpack_tender(archive, RULES, LIMITS, _bid_number)


def test_too_much_unpacked_data_is_refused() -> None:
    archive = _zip({"bid.pdf": BID, "big.pdf": b"0" * (2 * 1024 * 1024)})
    with pytest.raises(InvalidInputError, match="more than 1 MB"):
        unpack_tender(archive, RULES, LIMITS, _bid_number)


def test_office_files_are_not_tender_archives() -> None:
    docx = _zip({"word/document.xml": b"<w/>"})
    assert not is_zip_upload(docx)
    assert is_zip_upload(_zip({"bid.pdf": BID}))
    assert not is_zip_upload(BID)
