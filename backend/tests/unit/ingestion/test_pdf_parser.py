from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from app.core.config import Settings
from app.core.exceptions import DocumentParseError
from app.ingestion.ocr import OcrLine, OcrResult
from app.ingestion.pdf_parser import (
    ParserOptions,
    image_coverage,
    is_scanned,
    ocr_lines_to_blocks,
    open_pdf,
    parse_pdf,
)
from app.schemas.parsing import BBox, TextBlock
from tests.unit.ingestion.conftest import FakeOcrEngine, write_text_pdf

OPTIONS = ParserOptions(ocr_min_text_chars=50, ocr_min_image_coverage=0.5, ocr_dpi=300)
BOX = BBox(x0=0, y0=0, x1=1, y1=1)


def test_text_pdf_is_parsed_page_by_page_with_1_based_numbers(text_pdf: Path) -> None:
    doc = parse_pdf(text_pdf, OPTIONS, engine=None)
    assert doc.page_count == 2
    assert [p.page_number for p in doc.pages] == [1, 2]
    first = doc.pages[0]
    assert "Body material: SS316" in first.text
    assert first.ocr is False
    assert first.needs_ocr is False
    assert first.width == pytest.approx(595, abs=1)


def test_blocks_have_boxes_inside_the_page(text_pdf: Path) -> None:
    page = parse_pdf(text_pdf, OPTIONS, engine=None).pages[0]
    assert page.blocks
    for block in page.blocks:
        assert 0 <= block.bbox.x0 < block.bbox.x1 <= page.width
        assert 0 <= block.bbox.y0 < block.bbox.y1 <= page.height
        assert block.confidence is None


def test_blocks_are_in_reading_order(text_pdf: Path) -> None:
    blocks = parse_pdf(text_pdf, OPTIONS, engine=None).pages[0].blocks
    tops = [b.bbox.y0 for b in blocks]
    assert tops == sorted(tops)


def test_sparse_text_page_without_images_keeps_its_text_layer(
    tmp_path: Path, fake_ocr: Callable[[list[OcrLine]], FakeOcrEngine]
) -> None:
    # A cover page has very little text but is not a scan: OCR would only make it worse.
    pdf = write_text_pdf(tmp_path / "cover.pdf", [["Catalogue 2024"]])
    engine = fake_ocr([])
    page = parse_pdf(pdf, OPTIONS, engine).pages[0]
    assert engine.calls == []
    assert page.text == "Catalogue 2024"
    assert page.ocr is False
    assert page.needs_ocr is False


def test_scanned_page_without_engine_is_flagged_not_guessed(scanned_pdf: Path) -> None:
    page = parse_pdf(scanned_pdf, OPTIONS, engine=None).pages[0]
    assert page.text == ""
    assert page.ocr is False
    assert page.needs_ocr is True


def test_scanned_page_uses_ocr_and_scales_boxes_to_points(
    scanned_pdf: Path, fake_ocr: Callable[[list[OcrLine]], FakeOcrEngine]
) -> None:
    line = OcrLine(
        text="Body material: SS316", left=300, top=600, right=900, bottom=660, confidence=0.9
    )
    engine = fake_ocr([line])
    page = parse_pdf(scanned_pdf, OPTIONS, engine).pages[0]

    assert len(engine.calls) == 1
    assert engine.calls[0].startswith(b"\x89PNG")
    assert page.ocr is True
    assert page.needs_ocr is False
    assert page.text == "Body material: SS316"
    assert page.ocr_confidence == pytest.approx(0.9)
    # 300 px at 300 dpi = 1 inch = 72 pt
    assert page.blocks[0].bbox == BBox(x0=72, y0=144, x1=216, y1=158.4)


@pytest.mark.parametrize(
    ("text", "coverage", "expected"),
    [
        ("  a b \n c  ", 0.9, True),  # 3 non-space chars + mostly image -> scan
        ("abcd", 0.9, False),  # enough text layer
        ("", 0.2, False),  # sparse but no big image: blank or cover page
        ("", 0.5, True),  # coverage threshold is inclusive
    ],
)
def test_is_scanned_needs_sparse_text_and_image_coverage(
    text: str, coverage: float, expected: bool
) -> None:
    blocks = [TextBlock(text=text, bbox=BOX)]
    assert is_scanned(blocks, coverage, min_text_chars=4, min_image_coverage=0.5) is expected


def test_image_coverage(text_pdf: Path, scanned_pdf: Path) -> None:
    with pymupdf.open(text_pdf) as doc:
        assert image_coverage(doc[0]) == 0.0
    with pymupdf.open(scanned_pdf) as doc:
        assert image_coverage(doc[0]) == pytest.approx(1.0, abs=1e-3)


def test_ocr_lines_to_blocks_keeps_confidence() -> None:
    result = OcrResult(
        lines=[OcrLine(text="x", left=0, top=0, right=150, bottom=150, confidence=0.5)]
    )
    [block] = ocr_lines_to_blocks(result, dpi=150)
    assert block.bbox == BBox(x0=0, y0=0, x1=72, y1=72)
    assert block.confidence == 0.5


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DocumentParseError, match="not found"):
        open_pdf(tmp_path / "nope.pdf")


def test_corrupt_file_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(DocumentParseError, match="Not a readable PDF"):
        open_pdf(bad)


def test_encrypted_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    aes_256 = pymupdf.PDF_ENCRYPT_AES_256  # type: ignore[attr-defined]
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(path, encryption=aes_256, user_pw="u", owner_pw="o")
    doc.close()
    with pytest.raises(DocumentParseError, match="encrypted"):
        open_pdf(path)


def test_options_come_from_settings(settings: Settings) -> None:
    custom = settings.model_copy(
        update={"OCR_MIN_TEXT_CHARS": 7, "OCR_MIN_IMAGE_COVERAGE": 0.8, "OCR_DPI": 200}
    )
    assert ParserOptions.from_settings(custom) == ParserOptions(
        ocr_min_text_chars=7, ocr_min_image_coverage=0.8, ocr_dpi=200
    )
