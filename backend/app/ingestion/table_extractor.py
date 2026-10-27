"""Table extraction: PyMuPDF finds the cell grid, cell text is read by clipping each cell.

PyMuPDF's own `Table.extract()` text is not used: on test PDFs it dropped spaces
(`ETN 50` -> `ETN50`) and split decimals (`7.5` -> `75`, newline, `.`), which would
corrupt evidence.
Reading each cell box with the normal text extractor keeps the text exactly as printed.

Only vector tables are found; tables inside scanned images reach the chunker as OCR prose.
"""

import re
from collections.abc import Sequence
from typing import Final

import pymupdf

from app.schemas.chunking import ExtractedTable, TableRow
from app.schemas.parsing import BBox

_WHITESPACE: Final = re.compile(r"\s+")

Cell = str | None


def normalize_cell(text: str | None) -> str | None:
    """Collapse internal whitespace/newlines; keep None (a merged-cell continuation) as None.

    Args:
        text: Raw cell text, or None if the cell is covered by a merged cell.

    Returns:
        Single-line text, or None.
    """
    if text is None:
        return None
    return _WHITESPACE.sub(" ", text).strip()


def fill_merged_cells(rows: Sequence[Sequence[Cell]]) -> list[list[str]]:
    """Fill merged-cell continuations (None) from the cell above, column by column.

    Catalogue tables often merge the "model" cell across several rows; each row must still
    carry the model, or its numbers lose their meaning. Empty strings are real blanks and are
    not filled. A None in the first row has nothing above it and becomes "".

    Args:
        rows: Body rows, cells already normalized.

    Returns:
        Rows with every cell a string.
    """
    filled: list[list[str]] = []
    previous: list[str] = []
    for row in rows:
        current = [
            cell if cell is not None else (previous[i] if i < len(previous) else "")
            for i, cell in enumerate(row)
        ]
        filled.append(current)
        previous = current
    return filled


def _cell_text(page: pymupdf.Page, cell: tuple[float, float, float, float] | None) -> Cell:
    """Read the text inside one cell box, or None for a merged-cell continuation."""
    if cell is None:
        return None
    return normalize_cell(page.get_textbox(pymupdf.Rect(cell)))


def _to_bbox(rect: Sequence[float]) -> BBox:
    """Convert an (x0, y0, x1, y1) sequence to a `BBox`."""
    x0, y0, x1, y1 = (float(v) for v in rect)
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _header_names(page: pymupdf.Page, table: pymupdf.table.Table) -> list[str]:
    """Header labels read from the header cell boxes (blank labels become `Column N`)."""
    names = [_cell_text(page, cell) for cell in table.header.cells]
    return [name or f"Column {i + 1}" for i, name in enumerate(names)]


def _body_rows(page: pymupdf.Page, table: pymupdf.table.Table) -> list[pymupdf.table.TableRow]:
    """Table rows that are not the header (an external header sits above the table)."""
    rows = list(table.rows)
    return rows if table.header.external else rows[1:]


def extract_page_tables(page: pymupdf.Page) -> list[ExtractedTable]:
    """Find the tables on one page.

    Args:
        page: A PyMuPDF page with a text layer.

    Returns:
        Tables in PyMuPDF's detection order, each with header and filled body rows. Tables
        with no body rows are dropped.
    """
    tables: list[ExtractedTable] = []
    for table in page.find_tables().tables:
        body = _body_rows(page, table)
        raw_rows = [[_cell_text(page, cell) for cell in row.cells] for row in body]
        filled = fill_merged_cells(raw_rows)
        rows = [
            TableRow(cells=cells, bbox=_to_bbox(row.bbox))
            for cells, row in zip(filled, body, strict=True)
            if any(cells)
        ]
        if not rows:
            continue
        tables.append(
            ExtractedTable(
                page_number=page.number + 1,
                index=len(tables),
                bbox=_to_bbox(table.bbox),
                header=_header_names(page, table),
                rows=rows,
            )
        )
    return tables
