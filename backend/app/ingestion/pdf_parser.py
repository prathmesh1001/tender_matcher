"""Page-by-page PDF text extraction with bounding boxes and an OCR fallback for scanned pages.

Used for both manufacturer documents and tenders. Page numbers are 1-based so they match
what a reader sees and what citations show.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pymupdf

from app.core.config import Settings
from app.core.exceptions import DocumentParseError
from app.ingestion.glyph_repair import RepairOptions, page_repair, repair_text
from app.ingestion.ocr import OcrEngine, OcrResult
from app.schemas.parsing import BBox, ParsedDocument, ParsedPage, TextBlock, count_text_chars

logger = logging.getLogger(__name__)

PDF_POINTS_PER_INCH: Final = 72.0
_TEXT_BLOCK_TYPE: Final = 0  # PyMuPDF block type: 0 = text, 1 = image


@dataclass(frozen=True, slots=True)
class ParserOptions:
    """Tunable parsing behaviour (built from settings, so the parser never reads config)."""

    ocr_min_text_chars: int
    ocr_min_image_coverage: float
    ocr_dpi: int
    glyphs: RepairOptions | None = None  # repair shifted fonts; None = off

    @classmethod
    def from_settings(cls, settings: Settings) -> ParserOptions:
        """Build options from application settings."""
        return cls(
            ocr_min_text_chars=settings.OCR_MIN_TEXT_CHARS,
            ocr_min_image_coverage=settings.OCR_MIN_IMAGE_COVERAGE,
            ocr_dpi=settings.OCR_DPI,
            glyphs=RepairOptions.from_settings(settings),
        )


def open_pdf(path: Path) -> pymupdf.Document:
    """Open a PDF, rejecting missing, corrupt, encrypted or empty files.

    Args:
        path: The PDF file.

    Returns:
        An open PyMuPDF document (caller closes it).

    Raises:
        DocumentParseError: If the file cannot be used.
    """
    details = {"path": str(path)}
    if not path.is_file():
        raise DocumentParseError(f"PDF not found: {path}", details=details)
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise DocumentParseError(f"Not a readable PDF: {path}: {exc}", details=details) from exc
    if doc.needs_pass or doc.page_count == 0:
        reason = "encrypted" if doc.needs_pass else "empty"
        doc.close()
        raise DocumentParseError(f"PDF is {reason}: {path}", details=details)
    return doc


def extract_text_blocks(page: pymupdf.Page, glyphs: RepairOptions | None = None) -> list[TextBlock]:
    """Return the page's native text blocks in reading order (top-to-bottom, left-to-right).

    Args:
        page: A PyMuPDF page.
        glyphs: Shifted-font repair options (`glyph_repair`); None = text as extracted.

    Returns:
        Non-empty text blocks with their boxes in PDF points.
    """
    repair = page_repair(page, glyphs) if glyphs is not None else None
    blocks: list[TextBlock] = []
    for x0, y0, x1, y1, text, _num, block_type in page.get_text("blocks", sort=True):
        if block_type != _TEXT_BLOCK_TYPE or not str(text).strip():
            continue
        clean = repair_text(str(text), repair) if repair and repair.active else str(text)
        blocks.append(TextBlock(text=clean.strip(), bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1)))
    return blocks


def image_coverage(page: pymupdf.Page) -> float:
    """Return the fraction of the page area covered by images (0..1).

    Overlapping images are counted once each, so the sum is capped at 1.

    Args:
        page: A PyMuPDF page.

    Returns:
        Covered fraction of the page.
    """
    page_rect = page.rect
    page_area = float(page_rect.width) * float(page_rect.height)
    if page_area <= 0:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        clipped = pymupdf.Rect(info["bbox"]) & page_rect
        covered += max(float(clipped.width), 0.0) * max(float(clipped.height), 0.0)
    return min(covered / page_area, 1.0)


def is_scanned(
    blocks: list[TextBlock], coverage: float, *, min_text_chars: int, min_image_coverage: float
) -> bool:
    """Decide whether a page is a scan that needs OCR.

    Both conditions are required: a sparse page with no image (cover, short spec page) keeps
    its accurate text layer instead of being replaced by OCR output.

    Args:
        blocks: The page's native text blocks.
        coverage: Fraction of the page covered by images.
        min_text_chars: Below this many non-whitespace characters, the text layer is "sparse".
        min_image_coverage: At or above this image coverage, the page looks like a scan.

    Returns:
        True if the page should be OCR'd.
    """
    sparse_text = sum(count_text_chars(b.text) for b in blocks) < min_text_chars
    return sparse_text and coverage >= min_image_coverage


def ocr_lines_to_blocks(result: OcrResult, dpi: int) -> list[TextBlock]:
    """Convert OCR lines (image pixels at `dpi`) into text blocks in PDF points.

    Args:
        result: OCR output for the page image.
        dpi: Resolution the page was rendered at.

    Returns:
        One block per OCR line, with its confidence.
    """
    scale = PDF_POINTS_PER_INCH / dpi
    return [
        TextBlock(
            text=line.text,
            bbox=BBox(
                x0=line.left * scale,
                y0=line.top * scale,
                x1=line.right * scale,
                y1=line.bottom * scale,
            ),
            confidence=line.confidence,
        )
        for line in result.lines
    ]


def _native_page(page: pymupdf.Page, blocks: list[TextBlock], *, needs_ocr: bool) -> ParsedPage:
    """Build a page from its own text layer."""
    return ParsedPage(
        page_number=page.number + 1,
        text="\n".join(b.text for b in blocks),
        blocks=blocks,
        width=page.rect.width,
        height=page.rect.height,
        ocr=False,
        needs_ocr=needs_ocr,
    )


def _ocr_page(page: pymupdf.Page, engine: OcrEngine, dpi: int) -> ParsedPage:
    """Render a page and build it from OCR output."""
    image_png = page.get_pixmap(dpi=dpi).tobytes("png")
    result = engine.recognize(image_png)
    return ParsedPage(
        page_number=page.number + 1,
        text=result.text,
        blocks=ocr_lines_to_blocks(result, dpi),
        width=page.rect.width,
        height=page.rect.height,
        ocr=True,
        ocr_confidence=result.mean_confidence,
    )


def parse_page(page: pymupdf.Page, options: ParserOptions, engine: OcrEngine | None) -> ParsedPage:
    """Extract one page, falling back to OCR when it looks scanned.

    Args:
        page: A PyMuPDF page.
        options: Parser thresholds.
        engine: OCR engine, or None to only flag scanned pages with `needs_ocr`.

    Returns:
        The parsed page.
    """
    blocks = extract_text_blocks(page, options.glyphs)
    scanned = is_scanned(
        blocks,
        image_coverage(page),
        min_text_chars=options.ocr_min_text_chars,
        min_image_coverage=options.ocr_min_image_coverage,
    )
    if not scanned:
        return _native_page(page, blocks, needs_ocr=False)
    if engine is None:
        logger.warning("Page %d looks scanned but OCR is disabled", page.number + 1)
        return _native_page(page, blocks, needs_ocr=True)
    return _ocr_page(page, engine, options.ocr_dpi)


def parse_pdf(path: Path, options: ParserOptions, engine: OcrEngine | None) -> ParsedDocument:
    """Parse every page of a PDF.

    Args:
        path: The PDF file.
        options: Parser thresholds.
        engine: OCR engine for scanned pages, or None.

    Returns:
        All pages in order.

    Raises:
        DocumentParseError: If the PDF cannot be opened.
        ExternalServiceError: If OCR is needed and the engine fails.
    """
    with open_pdf(path) as doc:
        pages = [parse_page(page, options, engine) for page in doc]
    ocr_count = sum(p.ocr for p in pages)
    logger.info(
        "Parsed PDF", extra={"path": str(path), "pages": len(pages), "ocr_pages": ocr_count}
    )
    return ParsedDocument(pages=pages)
