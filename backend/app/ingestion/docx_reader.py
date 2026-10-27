"""Word (.docx) tender attachments (often the buyer's ATC / technical annexure).

A .docx has no fixed pages, so the whole document is one page (page 1) and citations say so.
The body is read in order straight from `word/document.xml` (standard library only):
paragraphs become text blocks and tables become `ExtractedTable`s, laid out top to bottom on a
virtual page so the normal chunker can apply its heading, section and table-row rules.

Merged cells follow the same rule as PDF tables: a vertically merged continuation repeats the
cell above it, a horizontally spanned cell repeats its own text across the columns it spans.
"""

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import (
    ElementTree as ET,
)

from app.core.exceptions import DocumentParseError
from app.ingestion.table_extractor import normalize_cell
from app.schemas.chunking import ExtractedTable, TableRow
from app.schemas.parsing import BBox, ParsedPage, TextBlock

_W: Final = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCUMENT_PART: Final = "word/document.xml"
_LINE_HEIGHT: Final = 10.0
_PAGE_WIDTH: Final = 600.0
DOCX_PAGE_NUMBER: Final = 1


@dataclass(frozen=True, slots=True)
class DocxTable:
    """Rows of a Word table; None marks a vertical-merge continuation."""

    rows: list[list[str | None]]


DocxItem = str | DocxTable


def _text(element: ET.Element) -> str:
    """All `w:t` text inside an element, paragraphs separated by spaces."""
    paragraphs = ["".join(t.text or "" for t in p.iter(f"{_W}t")) for p in element.iter(f"{_W}p")]
    return normalize_cell(" ".join(paragraphs)) or ""


def _cell_values(cell: ET.Element) -> list[str | None]:
    """Value(s) of one `w:tc`: repeated over its grid span; None if it continues a vMerge."""
    props = cell.find(f"{_W}tcPr")
    span, continued = 1, False
    if props is not None:
        grid = props.find(f"{_W}gridSpan")
        span = int(grid.get(f"{_W}val", "1")) if grid is not None else 1
        merge = props.find(f"{_W}vMerge")
        continued = merge is not None and merge.get(f"{_W}val") != "restart"
    value = None if continued else _text(cell)
    return [value] * span


def _table(element: ET.Element) -> DocxTable:
    """Read a `w:tbl` into rows of cell values."""
    rows = [
        [v for cell in row.findall(f"{_W}tc") for v in _cell_values(cell)]
        for row in element.findall(f"{_W}tr")
    ]
    return DocxTable(rows=[r for r in rows if any(r)])


def read_docx_items(path: Path) -> list[DocxItem]:
    """Body paragraphs (non-empty) and tables, in document order.

    Raises:
        DocumentParseError: If the file is not a readable .docx.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read(_DOCUMENT_PART))  # noqa: S314 - local Office file
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise DocumentParseError(f"Cannot read {path}: {exc}", details={"path": str(path)}) from exc
    body = root.find(f"{_W}body")
    items: list[DocxItem] = []
    for child in body if body is not None else []:
        if child.tag == f"{_W}p" and (text := _text(child)):
            items.append(text)
        elif child.tag == f"{_W}tbl":
            items.append(_table(child))
    return items


def _fill_down(rows: list[list[str | None]]) -> list[list[str]]:
    """Replace vertical-merge continuations with the value above them."""
    filled: list[list[str]] = []
    for row in rows:
        above = filled[-1] if filled else []
        filled.append(
            [
                c if c is not None else (above[i] if i < len(above) else "")
                for i, c in enumerate(row)
            ]
        )
    return filled


def _box(top: float, lines: int) -> BBox:
    """Virtual box `lines` tall starting at `top`."""
    return BBox(x0=0, y0=top, x1=_PAGE_WIDTH, y1=top + lines * _LINE_HEIGHT)


def _as_extracted(table: DocxTable, index: int, top: float) -> ExtractedTable:
    """Lay a Word table out on the virtual page; its first row is the header."""
    rows = _fill_down(table.rows)
    width = max(len(r) for r in rows)
    padded = [(r + [""] * width)[:width] for r in rows]
    header = [h or f"Column {i + 1}" for i, h in enumerate(padded[0])]
    body = [
        TableRow(cells=r, bbox=_box(top + (i + 1) * _LINE_HEIGHT, 1))
        for i, r in enumerate(padded[1:])
    ]
    return ExtractedTable(
        page_number=DOCX_PAGE_NUMBER,
        index=index,
        bbox=_box(top, len(padded)),
        header=header,
        rows=body,
    )


def docx_page(items: list[DocxItem]) -> tuple[ParsedPage, list[ExtractedTable]]:
    """Build the virtual page (paragraph blocks + tables in order) for the chunker.

    Args:
        items: Output of `read_docx_items`.

    Returns:
        The page and its tables (tables with fewer than two rows are kept as text blocks).
    """
    blocks: list[TextBlock] = []
    tables: list[ExtractedTable] = []
    lines: list[str] = []
    top = 0.0
    for item in items:
        if isinstance(item, DocxTable) and len(item.rows) >= 2:
            table = _as_extracted(item, len(tables), top)
            tables.append(table)
            lines.append(" | ".join(item.rows[0][i] or "" for i in range(len(item.rows[0]))))
            lines.extend(" | ".join(c for c in r.cells if c) for r in table.rows)
            top = table.bbox.y1 + _LINE_HEIGHT
            continue
        text = item if isinstance(item, str) else " | ".join(c or "" for r in item.rows for c in r)
        blocks.append(TextBlock(text=text, bbox=_box(top, 1)))
        lines.append(text)
        top += _LINE_HEIGHT * 2
    page = ParsedPage(
        page_number=DOCX_PAGE_NUMBER,
        text="\n".join(lines),
        blocks=blocks,
        width=_PAGE_WIDTH,
        height=top or _LINE_HEIGHT,
        ocr=False,
    )
    return page, tables
