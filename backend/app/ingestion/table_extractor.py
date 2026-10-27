"""Table extraction: PyMuPDF finds the cell grid, cell text is read by clipping each cell.

PyMuPDF's own `Table.extract()` text is not used: on test PDFs it dropped spaces
(`ETN 50` -> `ETN50`) and split decimals (`7.5` -> `75`, newline, `.`), which would
corrupt evidence.
Reading each cell box with the normal text extractor keeps the text exactly as printed.

Only vector tables are found; tables inside scanned images reach the chunker as OCR prose.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import pymupdf

from app.schemas.chunking import ExtractedTable, TableRow
from app.schemas.parsing import BBox

_WHITESPACE: Final = re.compile(r"\s+")
_PLACEHOLDER_HEADER: Final = re.compile(r"^Column \d+$")

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


@dataclass(frozen=True, slots=True)
class GridCell:
    """Text of one real (non-continuation) cell and the box it covers, merged spans included."""

    text: str
    bbox: BBox


def _column_spans(rows: Sequence[Sequence[GridCell | None]]) -> list[tuple[float, float] | None]:
    """x-range of each column, taken from a cell that does not span into its right neighbour.

    A cell followed by a real cell (or in the last column) is exactly one column wide.
    """
    width = max((len(r) for r in rows), default=0)
    spans: list[tuple[float, float] | None] = [None] * width
    for row in rows:
        for i, cell in enumerate(row):
            single = i == len(row) - 1 or row[i + 1] is not None
            if cell is not None and single and spans[i] is None:
                spans[i] = (cell.bbox.x0, cell.bbox.x1)
    return spans


def _contains(box: BBox, x: float, y: float) -> bool:
    """True if the point lies inside `box`."""
    return box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1


def _covering_text(
    rows: Sequence[Sequence[GridCell | None]], r: int, i: int, x: float, y: float
) -> str:
    """Text of the real cell (left in this row, or above in earlier rows) covering a point."""
    for cell in reversed(rows[r][:i]):
        if cell is not None:
            if _contains(cell.bbox, x, y):
                return cell.text
            break
    for row in reversed(rows[:r]):
        cell = row[i] if i < len(row) else None
        if cell is not None:
            return cell.text if _contains(cell.bbox, x, y) else ""
    return ""


def fill_merged_cells(
    rows: Sequence[Sequence[GridCell | None]], row_boxes: Sequence[BBox]
) -> list[list[str]]:
    """Fill merged-cell continuations (None) with the text of the cell that spans over them.

    Catalogue tables merge cells both ways: a "model" cell down several rows, and a value
    across several grade columns (e.g. "Packing: Graphite" over every grade). Each continuation
    takes the text of whichever real cell geometrically covers it: the cell to its left for a
    horizontal span, the cell above for a vertical one. Copying from the row above blindly
    would put a neighbouring row's values into a horizontal span and invent claims.
    Empty strings are real blanks and are not filled; a continuation nothing covers becomes "".

    Args:
        rows: Body rows; None marks a continuation of a merged cell.
        row_boxes: Box of each row, same length as `rows`.

    Returns:
        Rows with every cell a string.
    """
    spans = _column_spans(rows)
    filled: list[list[str]] = []
    for r, (row, row_box) in enumerate(zip(rows, row_boxes, strict=True)):
        y = (row_box.y0 + row_box.y1) / 2
        current: list[str] = []
        for i, cell in enumerate(row):
            if cell is not None:
                current.append(cell.text)
                continue
            span = spans[i]
            x = (span[0] + span[1]) / 2 if span else row_box.x1
            current.append(_covering_text(rows, r, i, x, y))
        filled.append(current)
    return filled


def _cell_text(page: pymupdf.Page, cell: tuple[float, float, float, float] | None) -> Cell:
    """Read the text inside one cell box, or None for a merged-cell continuation."""
    if cell is None:
        return None
    return normalize_cell(page.get_textbox(pymupdf.Rect(cell)))


def _grid_cell(
    page: pymupdf.Page, cell: tuple[float, float, float, float] | None
) -> GridCell | None:
    """Read one cell with its box, or None for a merged-cell continuation."""
    if cell is None:
        return None
    return GridCell(text=_cell_text(page, cell) or "", bbox=_to_bbox(cell))


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
        grid = [[_grid_cell(page, cell) for cell in row.cells] for row in body]
        filled = fill_merged_cells(grid, [_to_bbox(row.bbox) for row in body])
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


def _ends_near_bottom(table: ExtractedTable, page_height: float, edge: float) -> bool:
    """True if the table reaches into the bottom `edge` fraction of its page."""
    return table.bbox.y1 >= page_height * (1 - edge)


def _starts_near_top(table: ExtractedTable, page_height: float, edge: float) -> bool:
    """True if the table starts within the top `edge` fraction of its page."""
    return table.bbox.y0 <= page_height * edge


def _header_as_row(table: ExtractedTable) -> TableRow:
    """Turn a misdetected header (really the first data row) back into a body row."""
    cells = ["" if _PLACEHOLDER_HEADER.match(h) else h for h in table.header]
    bottom = table.rows[0].bbox.y0 if table.rows else table.bbox.y1
    return TableRow(cells=cells, bbox=table.bbox.model_copy(update={"y1": bottom}))


def continue_table(previous: ExtractedTable, table: ExtractedTable) -> ExtractedTable:
    """Give a continuation table its predecessor's header.

    If the continuation repeats the header it is simply reused; otherwise what was detected as
    its header is really its first data row and becomes a body row. Body rows that repeat the
    header are dropped.

    Args:
        previous: The table on the earlier page (already continued, if it was itself one).
        table: The table that continues it.

    Returns:
        The continuation with the earlier header and `continued_from_page` set.
    """
    rows = list(table.rows)
    if table.header != previous.header:
        rows.insert(0, _header_as_row(table))
    rows = [row for row in rows if row.cells != previous.header]
    return table.model_copy(
        update={
            "header": previous.header,
            "rows": rows,
            "continued_from_page": previous.continued_from_page or previous.page_number,
        }
    )


def merge_continued_tables(
    tables_by_page: Mapping[int, Sequence[ExtractedTable]],
    page_heights: Mapping[int, float],
    edge_fraction: float,
) -> dict[int, list[ExtractedTable]]:
    """Link tables that run over a page break, so continuation rows keep their header.

    A table continues the previous page's last table when that one ends near the bottom of its
    page, this one is the first on its page and starts near the top, and both have the same
    number of columns. Tables stay on their own pages, so citations keep the right page.

    Args:
        tables_by_page: Tables keyed by 1-based page number, in page order within each page.
        page_heights: Height of each page in points.
        edge_fraction: How close to the page edge (as a fraction of height) counts as "near".

    Returns:
        A new mapping with continuation tables rewritten.
    """
    merged: dict[int, list[ExtractedTable]] = {}
    for page_number in sorted(tables_by_page):
        tables = list(tables_by_page[page_number])
        previous_tables = merged.get(page_number - 1, [])
        if tables and previous_tables:
            previous, first = previous_tables[-1], tables[0]
            if (
                _ends_near_bottom(previous, page_heights[page_number - 1], edge_fraction)
                and _starts_near_top(first, page_heights[page_number], edge_fraction)
                and len(previous.header) == len(first.header)
            ):
                tables[0] = continue_table(previous, first)
        merged[page_number] = tables
    return merged
