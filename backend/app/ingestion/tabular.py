"""Spreadsheet-like tender files (BOQ line items `.csv`, ATC `.xlsx`) as pages and row chunks.

Each CSV file is one "page"; each XLSX sheet is one page, numbered in sheet order, so evidence
cites `sheet N` the same way it cites a PDF page. The first non-empty row is the header, and
every later row becomes a table-row chunk with the header repeated, like PDF tables.
"""

import csv
import io
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from openpyxl import load_workbook

from app.core.exceptions import DocumentParseError
from app.ingestion.chunker import ChunkerOptions, table_chunks
from app.ingestion.table_extractor import normalize_cell
from app.schemas.chunking import ChunkDraft, ExtractedTable, TableRow
from app.schemas.parsing import BBox

_CSV_ENCODINGS: Final = ("utf-8-sig", "cp1252")
_NO_BOX: Final = BBox(x0=0, y0=0, x1=0, y1=0)
CELL_SEPARATOR: Final = " | "


@dataclass(frozen=True, slots=True)
class Sheet:
    """One page-equivalent of a tabular file: its name and cleaned rows."""

    page_number: int
    name: str
    rows: list[list[str]]
    # Per row, per cell: the cell's own lines (a BOQ description has one "KEY : value" each).
    lines: list[list[list[str]]] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Verbatim-ish page text: one line per row, cells joined by `CELL_SEPARATOR`."""
        return "\n".join(CELL_SEPARATOR.join(c for c in row if c) for row in self.rows)


def _cell_lines(value: object) -> list[str]:
    """A cell's non-empty lines, each whitespace-normalized."""
    text = "" if value is None else str(value)
    return [line for line in (normalize_cell(x) or "" for x in text.splitlines()) if line]


def _clean_rows(rows: Sequence[Sequence[object]]) -> tuple[list[list[str]], list[list[list[str]]]]:
    """Stringify and whitespace-normalize cells; drop rows that are entirely empty.

    Returns:
        The cleaned rows and, per kept row, each cell's lines.
    """
    kept: list[list[str]] = []
    lines: list[list[list[str]]] = []
    for row in rows:
        cells = [normalize_cell("" if c is None else str(c)) or "" for c in row]
        if any(cells):
            kept.append(cells)
            lines.append([_cell_lines(c) for c in row])
    return kept, lines


def read_csv(path: Path) -> list[Sheet]:
    """Read a CSV file as one sheet, trying UTF-8 (with BOM) then Windows-1252.

    Raises:
        DocumentParseError: If the file cannot be read or decoded.
    """
    raw = path.read_bytes()
    for encoding in _CSV_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        rows, lines = _clean_rows(list(csv.reader(io.StringIO(text))))
        return [Sheet(page_number=1, name=path.name, rows=rows, lines=lines)]
    raise DocumentParseError(f"Cannot decode CSV {path}", details={"path": str(path)})


def read_xlsx(path: Path) -> list[Sheet]:
    """Read every worksheet of an XLSX file (cell values, not formulas).

    Raises:
        DocumentParseError: If the workbook cannot be opened.
    """
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise DocumentParseError(f"Cannot open {path}: {exc}", details={"path": str(path)}) from exc
    try:
        sheets: list[Sheet] = []
        for i, ws in enumerate(workbook.worksheets, start=1):
            rows, lines = _clean_rows(list(ws.values))
            sheets.append(Sheet(page_number=i, name=ws.title, rows=rows, lines=lines))
        return sheets
    finally:
        workbook.close()


def _pad(row: list[str], width: int) -> list[str]:
    """Pad or trim a row to `width` cells."""
    return (row + [""] * width)[:width]


def _row_lines(sheet: Sheet, row: int) -> list[list[str]]:
    """The row's cell lines, kept only when a cell has several (same rule as PDF rows)."""
    if row >= len(sheet.lines) or all(len(c) <= 1 for c in sheet.lines[row]):
        return []
    width = len(sheet.rows[0])
    return (sheet.lines[row] + [[]] * width)[:width]


def sheet_chunks(sheet: Sheet, options: ChunkerOptions) -> list[ChunkDraft]:
    """One table-row chunk per data row, header and sheet name repeated in each.

    Args:
        sheet: A sheet with at least a header row.
        options: Chunker options (model-column keywords).

    Returns:
        Row chunks with positions in sheet order; empty if the sheet has no data rows.
    """
    if len(sheet.rows) < 2:
        return []
    header = [h or f"Column {i + 1}" for i, h in enumerate(sheet.rows[0])]
    table = ExtractedTable(
        page_number=sheet.page_number,
        index=0,
        bbox=_NO_BOX,
        header=header,
        rows=[
            TableRow(cells=_pad(r, len(header)), bbox=_NO_BOX, cell_lines=_row_lines(sheet, i))
            for i, r in enumerate(sheet.rows[1:], start=1)
        ],
    )
    chunks = table_chunks(table, sheet.name, options)
    return [c.model_copy(update={"position": i}) for i, c in enumerate(chunks)]
