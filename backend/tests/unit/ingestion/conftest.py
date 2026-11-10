"""Synthetic PDFs built with PyMuPDF for parser tests (no real catalogue content)."""

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from app.ingestion.ocr import OcrLine, OcrResult

TEXT_LINES = ["Pump Model X-100", "Body material: SS316", "Max head: 45 m"]


def write_text_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Write a PDF whose pages carry a real text layer."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 72 + i * 20), line, fontsize=12)
    doc.save(path)
    doc.close()
    return path


def write_scanned_pdf(path: Path, lines: list[str]) -> Path:
    """Write a one-page PDF that is only an image of text (no text layer), like a scan."""
    source = pymupdf.open()
    page = source.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + i * 24), line, fontsize=16)
    pixmap = page.get_pixmap(dpi=200)
    source.close()

    scanned = pymupdf.open()
    target = scanned.new_page()
    target.insert_image(target.rect, pixmap=pixmap)
    scanned.save(path)
    scanned.close()
    return path


@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    return write_text_pdf(tmp_path / "text.pdf", [TEXT_LINES, ["Second page: flow 120 m3/h"]])


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    return write_scanned_pdf(tmp_path / "scanned.pdf", TEXT_LINES)


class FakeOcrEngine:
    """Returns fixed lines and records how often it was called."""

    def __init__(self, lines: list[OcrLine]) -> None:
        self.lines = lines
        self.calls: list[bytes] = []

    def recognize(self, image_png: bytes, page_segmentation: int | None = None) -> OcrResult:
        self.calls.append(image_png)
        return OcrResult(lines=self.lines)


@pytest.fixture
def fake_ocr() -> Callable[[list[OcrLine]], FakeOcrEngine]:
    return FakeOcrEngine


TABLE_HEADER = ["Model", "Flow (m3/h)", "Head (m)", "Power (kW)"]
# None = cell merged with the one above (no separating line is drawn).
TABLE_ROWS: list[list[str | None]] = [
    ["ETN 50", "120", "32", "7.5"],
    [None, "150", "28", "7.5"],
    ["ETN 65", "200", "45", "15"],
]


def draw_table(page: pymupdf.Page, top: float = 80) -> None:
    """Draw a ruled table whose first column merges rows 1-2, like many catalogues do."""
    cols = [72, 172, 272, 372, 472]
    ys = [top + 20 * i for i in range(len(TABLE_ROWS) + 2)]
    for i, y in enumerate(ys):
        start = cols[1] if i == 2 else cols[0]  # no line under "ETN 50" in column 0
        page.draw_line((start, y), (cols[-1], y))
    for x in cols:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for j, label in enumerate(TABLE_HEADER):
        page.insert_text((cols[j] + 4, ys[0] + 14), label, fontsize=9)
    for i, row in enumerate(TABLE_ROWS):
        for j, cell in enumerate(row):
            if cell:
                page.insert_text((cols[j] + 4, ys[i + 1] + 14), cell, fontsize=9)


def write_catalogue_pdf(path: Path) -> Path:
    """Heading, a table under it, then a materials paragraph and a second heading."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "ETN SERIES PERFORMANCE DATA", fontsize=13)
    draw_table(page, top=80)
    page.insert_text((72, 200), "Materials", fontsize=12)
    page.insert_text((72, 220), "Casing in grey cast iron, impeller in bronze.", fontsize=10)
    page.insert_text((72, 260), "Shaft Seal", fontsize=12)
    page.insert_text((72, 280), "Mechanical seal as standard.", fontsize=10)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def catalogue_pdf(tmp_path: Path) -> Path:
    return write_catalogue_pdf(tmp_path / "catalogue.pdf")
