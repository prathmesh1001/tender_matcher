"""Table extraction: PyMuPDF finds the cell grid; each cell takes the words centred inside it.

PyMuPDF's own `Table.extract()` text is not used: on test PDFs it dropped spaces
(`ETN 50` -> `ETN50`) and split decimals (`7.5` -> `75`, newline, `.`), which would
corrupt evidence.
Words come from the normal text extractor, read once per page, so text stays as printed.

Only vector tables are found; tables inside scanned images reach the chunker as OCR prose.

Key-value tables: GeM bid documents are two-column `label | value` tables, often under a title
row spanning both columns ("Bid Details"). Read as normal tables, the title plus the first data
row became the "header" and was repeated in front of every row. For tender files, two-column
tables are therefore read as label/value pairs (`key_value=True`). Catalogues keep normal
headers: their two-column tables have real ones ("Parameter | Standard").

Stacked items: some buyer spec tables draw no line between line items, so PyMuPDF returns one
tall row whose cells hold every item stacked ("10" over "20", "3.000" over "6.000"). Such a
row is split where each item number starts, and every cell is re-read inside that
slice, so each line item gets its own row (CLAUDE.md D5).

Continuation lines: a wrapped cell line read as a row of its own ("/Stainless Steel") is
merged back into the row above (`table_cleanup.merge_continuation_rows`, failure case V4).
A value cut in two by a column line ("Up to 16" | "bar") is joined again and given to every
column it spans (`table_cleanup.merge_split_values`, failure case V5). A caption read as the
header row is moved to `title` (`table_cleanup.split_caption_header`, failure cases V2, V3).

Tables drawn with thin filled rectangles instead of lines are invisible to PyMuPDF's finder;
on pages where it finds nothing, the grid is rebuilt from the drawn rules (`layout`,
failure case T1) and read the same way.
"""

import itertools
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import pymupdf

from app.ingestion.glyph_repair import RepairOptions, page_repair, repair_word
from app.ingestion.layout import (
    LayoutOptions,
    Rule,
    RuleGrid,
    band_box,
    grid_cells,
    page_grids,
    rule_from_box,
)
from app.ingestion.table_cleanup import (
    TableCleanupOptions,
    merge_continuation_rows,
    merge_split_values,
    split_caption_header,
)
from app.schemas.chunking import ExtractedTable, TableRow
from app.schemas.parsing import BBox

_WHITESPACE: Final = re.compile(r"\s+")
_PLACEHOLDER_HEADER: Final = re.compile(r"^Column \d+$")
_NUMERIC: Final = re.compile(r"^[\d\s.,/()%+-]+$")
KEY_VALUE_COLUMNS: Final = 2
# An item number in the first filled column: "10", "1.2", "A3", "4." or "5)".
_ITEM_ID: Final = re.compile(r"^[A-Za-z]{0,2}\d{1,4}(?:\.\d{1,3})*[A-Za-z]?[.)]?$")
# Two text lines start "at the same height" within this many points (PDF geometry, not tuning).
_LINE_Y_TOLERANCE: Final = 2.0

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


Word = tuple[float, float, float, float, str, int, int, int]


def drop_overprints(words: Iterable[Word]) -> tuple[Word, ...]:
    """Drop words printed again on top of an identical word (fake bold), keeping the first.

    Args:
        words: PyMuPDF words `(x0, y0, x1, y1, text, block, line, word_no)`.

    Returns:
        The words, each overprinted copy removed.
    """
    kept: list[Word] = []
    seen: dict[str, list[Word]] = {}
    for w in words:
        same = seen.setdefault(w[4], [])
        if any(
            abs(w[0] - o[0]) <= _LINE_Y_TOLERANCE and abs(w[1] - o[1]) <= _LINE_Y_TOLERANCE
            for o in same
        ):
            continue
        same.append(w)
        kept.append(w)
    return tuple(kept)


def _distinct(ys: Iterable[float]) -> list[float]:
    """Heights more than the line tolerance apart (lines side by side count once)."""
    out: list[float] = []
    for y in sorted(ys):
        if not out or y - out[-1] > _LINE_Y_TOLERANCE:
            out.append(y)
    return out


@dataclass(frozen=True, slots=True)
class PageWords:
    """Every word of one page, read once; cells take the words whose centre lies inside them.

    Reading each cell box with `get_textbox` also picked up text that only touched the box, so
    a thin row got the line below it too ("01 02 | Body Body Seat Ring"), mixing two rows. It
    also re-extracted the whole page for every cell, which made big catalogues very slow.

    Some catalogues fake bold by printing every word twice, a point apart ("250 250"); such
    overprinted copies are dropped so each value is read once.
    """

    words: tuple[Word, ...]

    @classmethod
    def of(cls, page: pymupdf.Page, glyphs: RepairOptions | None = None) -> PageWords:
        """Read the page's words once, without overprinted duplicates, shifted fonts repaired."""
        words = [tuple(w[:8]) for w in page.get_text("words")]
        repair = page_repair(page, glyphs) if glyphs is not None else None
        if repair is not None and repair.active:
            words = [(*w[:4], repair_word(str(w[4]), w[:4], repair), *w[5:]) for w in words]
        return cls(words=drop_overprints(words))

    def lines(self, box: BBox) -> list[tuple[float, str]]:
        """`(y0, text)` of each text line whose words are centred inside `box`, top to bottom."""
        grouped: dict[tuple[int, int], list[tuple[float, float, int, str]]] = {}
        for x0, y0, x1, y1, word, block, line, number in self.words:
            if box.x0 <= (x0 + x1) / 2 <= box.x1 and box.y0 <= (y0 + y1) / 2 <= box.y1:
                grouped.setdefault((block, line), []).append((y0, x0, number, word))
        lines = [
            (
                min(w[0] for w in ws),
                min(w[1] for w in ws),
                " ".join(w[3] for w in sorted(ws, key=lambda t: t[2])),
            )
            for ws in grouped.values()
        ]
        return [(y, text) for y, _, text in sorted(lines)]

    def text(self, box: BBox) -> str | None:
        """Single-line text inside `box`, or None if it holds no words."""
        return normalize_cell(" ".join(text for _, text in self.lines(box))) or None


def with_paragraph_breaks(lines: Sequence[tuple[float, str]], gap_factor: float) -> list[str]:
    """Line texts, with "" where the gap to the previous line is a paragraph break.

    A break is a gap more than `gap_factor` times the cell's median line pitch (8119613: the
    closing sentence sits 17 pt below the last "KEY : value" line, lines are 8.6 pt apart).

    Args:
        lines: `(y0, text)` of one cell's lines, top to bottom.
        gap_factor: Multiple of the median pitch that starts a new paragraph.

    Returns:
        The texts, with "" between paragraphs.
    """
    return [text for _, text in positioned_lines(lines, gap_factor)]


def positioned_lines(
    lines: Sequence[tuple[float, str]], gap_factor: float
) -> list[tuple[float | None, str]]:
    """`with_paragraph_breaks`, keeping each line's top y (None for a break)."""
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(lines)]
    pitch = statistics.median(gaps) if gaps else 0.0
    out: list[tuple[float | None, str]] = []
    for i, (y, text) in enumerate(lines):
        if i and pitch and gaps[i - 1] > gap_factor * pitch:
            out.append((None, ""))
        out.append((y, text))
    return out


def cell_lines(
    words: PageWords, boxes: Sequence[BBox | None], gap_factor: float
) -> list[list[str]]:
    """Text lines of each cell box, or [] when no cell holds more than one line.

    `cells` joins a cell's lines with spaces; tender specs print one "KEY : value" per
    line, so the lines are kept as well for splitting requirements by position.

    Args:
        words: The page's words.
        boxes: One box per cell (None for a merged-cell continuation).
        gap_factor: Paragraph-break gap (see `with_paragraph_breaks`).

    Returns:
        Lines per cell ("" marks a paragraph break), or [] if every cell is a single line.
    """
    return [[text for _, text in cell] for cell in _positioned_cells(words, boxes, gap_factor)]


def cell_line_ys(
    words: PageWords, boxes: Sequence[BBox | None], gap_factor: float
) -> list[list[float | None]]:
    """Top y of each line `cell_lines` returns (None at a break), [] when it returns []."""
    return [[y for y, _ in cell] for cell in _positioned_cells(words, boxes, gap_factor)]


def _positioned_cells(
    words: PageWords, boxes: Sequence[BBox | None], gap_factor: float
) -> list[list[tuple[float | None, str]]]:
    lines = [positioned_lines(words.lines(b), gap_factor) if b is not None else [] for b in boxes]
    return lines if any(len(cell) > 1 for cell in lines) else []


def _cell_text(words: PageWords, cell: tuple[float, float, float, float] | None) -> Cell:
    """Read the text inside one cell box, or None for a merged-cell continuation."""
    if cell is None:
        return None
    return words.text(_to_bbox(cell)) or ""


def _grid_cell(words: PageWords, cell: tuple[float, float, float, float] | None) -> GridCell | None:
    """Read one cell with its box, or None for a merged-cell continuation."""
    if cell is None:
        return None
    return GridCell(text=_cell_text(words, cell) or "", bbox=_to_bbox(cell))


def item_starts(
    anchor_lines: Sequence[tuple[float, str]], other_line_ys: Sequence[Sequence[float]]
) -> list[float]:
    """Top y of each line item stacked in one row, or [] if the row holds a single item.

    The row is split only if every line of the anchor cell (first filled column) is an item
    number and at least one other column has a line starting at each of those heights, so
    ordinary wrapped text is never split.

    Args:
        anchor_lines: `(y0, text)` of each line in the anchor cell, top to bottom.
        other_line_ys: Line y0s of every other cell in the row.

    Returns:
        Item start heights, top to bottom.
    """
    if len(anchor_lines) < 2 or not all(_ITEM_ID.match(text) for _, text in anchor_lines):
        return []
    starts = [y for y, _ in anchor_lines]

    def aligned(ys: Sequence[float]) -> bool:
        return all(any(abs(s - y) <= _LINE_Y_TOLERANCE for y in ys) for s in starts)

    return starts if any(aligned(ys) for ys in other_line_ys) else []


def _slice(words: PageWords, cell: GridCell | None, y0: float, y1: float) -> GridCell | None:
    """Re-read one cell between two heights (a merged-cell continuation stays None)."""
    if cell is None:
        return None
    box = cell.bbox.model_copy(update={"y0": y0, "y1": y1})
    return GridCell(text=words.text(box) or "", bbox=box)


@dataclass(frozen=True, slots=True)
class GridRow:
    """One body row's cells and box; `stacked_unresolved` marks items that could not be split."""

    cells: list[GridCell | None]
    box: BBox
    stacked_unresolved: bool = False


def _is_stacked_anchor(cell: GridCell) -> bool:
    """True if a cell holds only item numbers, two or more of them ("01 02")."""
    tokens = cell.text.split()
    return len(tokens) >= 2 and all(_ITEM_ID.match(t) for t in tokens)


def split_stacked_row(
    words: PageWords, cells: Sequence[GridCell | None], row_box: BBox
) -> list[GridRow]:
    """Split a row that holds several items stacked without a dividing line.

    The split is made only when it is safe: every item number starts inside the row and lines
    up with a line in another column, and every slice keeps its item number plus other text.
    Otherwise the row is kept whole and flagged `stacked_unresolved`, so its evidence is
    reviewed instead of trusted (never guess which material belongs to which part).

    Args:
        words: Words of the page the table is on.
        cells: The row's cells (None for merged-cell continuations).
        row_box: The row's box.

    Returns:
        One row per item; the row unchanged if it holds a single item.
    """
    whole = [GridRow(list(cells), row_box)]
    filled = [c for c in cells if c is not None and c.text]
    if not filled or not _is_stacked_anchor(filled[0]):  # cheap test before word positions
        return whole
    anchor_lines = words.lines(filled[0].bbox)
    if len(_distinct(y for y, _ in anchor_lines)) < 2:  # side by side: merged columns, no stack
        return whole
    others = [[y for y, _ in words.lines(c.bbox)] for c in filled[1:]]
    starts = item_starts(anchor_lines, others)
    inside = all(row_box.y0 - _LINE_Y_TOLERANCE <= s <= row_box.y1 for s in starts)
    if not starts or not inside:
        return [GridRow(list(cells), row_box, stacked_unresolved=True)]
    tops = [row_box.y0, *(s - _LINE_Y_TOLERANCE for s in starts[1:])]
    bottoms = [*tops[1:], row_box.y1]
    parts = [
        GridRow(
            [_slice(words, c, top, bottom) for c in cells],
            row_box.model_copy(update={"y0": top, "y1": bottom}),
        )
        for top, bottom in zip(tops, bottoms, strict=True)
    ]
    if any(sum(1 for c in part.cells if c is not None and c.text) < 2 for part in parts):
        return [GridRow(list(cells), row_box, stacked_unresolved=True)]
    return parts


def _to_bbox(rect: Sequence[float]) -> BBox:
    """Convert an (x0, y0, x1, y1) sequence to a `BBox`."""
    x0, y0, x1, y1 = (float(v) for v in rect)
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def header_names(raw: Sequence[str | None]) -> list[str]:
    """Header labels; a cell spanned by the one to its left (None) repeats that label.

    A genuinely blank header cell ("") becomes `Column N`.

    Args:
        raw: Header cell texts, None for cells covered by a horizontally merged header.

    Returns:
        One label per column.
    """
    names: list[str] = []
    for i, cell in enumerate(raw):
        if cell is None and names:
            names.append(names[-1])
        else:
            names.append(cell or f"Column {i + 1}")
    return names


def _spanned_columns(raw: Sequence[str | None]) -> set[int]:
    """Columns under a header cell that spans more than one column (anchor included)."""
    spanned: set[int] = set()
    for i, cell in enumerate(raw):
        if cell is None and i > 0:
            anchor = i - 1
            while anchor > 0 and raw[anchor] is None:
                anchor -= 1
            spanned.update(range(anchor, i + 1))
    return spanned


def is_sub_header(raw_header: Sequence[str | None], row: Sequence[str]) -> bool:
    """True if `row` is the second line of a two-row header (CLAUDE.md C3).

    Pattern: a header cell spans several columns (e.g. "Materials") and the next row names
    each spanned column (e.g. "WCB/ WCC", "LCB/ LCC") while the columns whose header spans
    vertically (e.g. "No.", "Components") are blank in that row.

    Args:
        raw_header: Header cell texts, None under a horizontal span.
        row: The first body row.

    Returns:
        Whether the row should be merged into the header.
    """
    spanned = _spanned_columns(raw_header)
    if not spanned or len(row) != len(raw_header):
        return False
    under_span = [row[i] for i in sorted(spanned)]
    elsewhere = [row[i] for i in range(len(row)) if i not in spanned]
    return all(under_span) and not any(elsewhere)


def merge_sub_header(header: Sequence[str], row: Sequence[str]) -> list[str]:
    """Combine header labels with a sub-header row: "Materials" + "WCB/ WCC"."""
    return [f"{h} {s}" if s else h for h, s in zip(header, row, strict=True)]


def _raw_header(words: PageWords, table: pymupdf.table.Table) -> list[str | None]:
    """Header cell texts; None where a horizontally merged header cell covers the column."""
    return [_cell_text(words, cell) for cell in table.header.cells]


def _body_rows(table: pymupdf.table.Table) -> list[pymupdf.table.TableRow]:
    """Table rows that are not the header (an external header sits above the table)."""
    rows = list(table.rows)
    return rows if table.header.external else rows[1:]


def key_value_layout(
    raw_header: Sequence[str | None], header_bbox: BBox, rows: Sequence[TableRow]
) -> tuple[str | None, list[TableRow]]:
    """Read a two-column table as label/value rows.

    A header cell spanning both columns is the table's title; any other header row is really
    the first label/value pair and becomes a body row.

    Args:
        raw_header: Header cell texts, None under a horizontal span.
        header_bbox: Box of the header row.
        rows: Body rows.

    Returns:
        `(title, rows)`.
    """
    if raw_header and raw_header[0] and all(cell is None for cell in raw_header[1:]):
        return raw_header[0], list(rows)
    first = TableRow(cells=[cell or "" for cell in raw_header], bbox=header_bbox)
    return None, [first, *rows] if any(first.cells) else list(rows)


def _header_and_rows(
    words: PageWords,
    table: pymupdf.table.Table,
    rows: list[TableRow],
    key_value: bool,
    max_label_words: int = 0,
) -> tuple[list[str], str | None, list[TableRow]]:
    """Header, title (a key-value title row or a caption) and body rows of one table."""
    raw_header = _raw_header(words, table)
    if key_value:
        if table.header.external:  # header text sits above the grid: rows are all pairs
            return [""] * len(raw_header), None, rows
        title, rows = key_value_layout(raw_header, _to_bbox(table.header.bbox), rows)
        return [""] * len(raw_header), title, rows
    header = header_names(raw_header)
    if rows and is_sub_header(raw_header, rows[0].cells):
        header, rows = merge_sub_header(header, rows[0].cells), rows[1:]
    elif rows and completes_header(header, rows[0].cells):
        header = [r or h for h, r in zip(header, rows[0].cells, strict=True)]
        rows = rows[1:]
    elif max_label_words:
        found = split_caption_header(header, rows, max_label_words)
        header, rows = found.header, found.rows
        return header, found.caption, [r for r in rows if not repeats_header(header, r.cells)]
    return header, None, [r for r in rows if not repeats_header(header, r.cells)]


def completes_header(header: Sequence[str], row: Sequence[str]) -> bool:
    """True if the first body row is the rest of a header split over two rows (CLAUDE.md C3).

    Labels in cells merged down two rows are printed in the lower half, so the header row reads
    blank there ("Column 1") and the labels arrive as the first body row. That row fills only
    blank header columns, with words, not numbers.

    Args:
        header: Header labels (blank cells named "Column N").
        row: The first body row.

    Returns:
        Whether the row should complete the header.
    """
    if len(row) != len(header):
        return False
    blank = [bool(_PLACEHOLDER_HEADER.match(h)) for h in header]
    fills = [c for c, b in zip(row, blank, strict=True) if b and c]
    clashes = any(c for c, b in zip(row, blank, strict=True) if not b)
    return bool(fills) and not clashes and not any(_NUMERIC.match(c) for c in fills)


def repeats_header(header: Sequence[str], cells: Sequence[str]) -> bool:
    """True if every filled cell of a body row is just its column's header label."""
    pairs = [(h, c) for h, c in zip(header, cells, strict=False) if c]
    return bool(pairs) and all(h == c for h, c in pairs)


def _grid_rows(words: PageWords, table: pymupdf.table.Table, split_stacked: bool) -> list[GridRow]:
    """Body rows as grid cells with their boxes, stacked line items split if asked."""
    rows = [
        GridRow([_grid_cell(words, cell) for cell in row.cells], _to_bbox(row.bbox))
        for row in _body_rows(table)
    ]
    if not split_stacked:
        return rows
    return [part for row in rows for part in split_stacked_row(words, row.cells, row.box)]


def drawn_rules(page: pymupdf.Page, max_thickness: float) -> list[Rule]:
    """Every thin rectangle or straight line drawn on the page, as a rule.

    Args:
        page: A PyMuPDF page.
        max_thickness: Thicker shapes are areas, not rules.

    Returns:
        The page's rules in drawing order.
    """
    rules: list[Rule] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "re":
                rect = pymupdf.Rect(item[1])
            elif item[0] == "l":
                rect = pymupdf.Rect(item[1], item[2]).normalize()
            else:
                continue
            box = BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)
            rule = rule_from_box(box, max_thickness)
            if rule is not None:
                rules.append(rule)
    return rules


def _band_texts(words: PageWords, boxes: Sequence[BBox]) -> list[str]:
    """Cell texts of one band; a cell merged with its left neighbour is read once."""
    return [
        "" if i and box == boxes[i - 1] else words.text(box) or "" for i, box in enumerate(boxes)
    ]


def _is_title_band(boxes: Sequence[BBox], texts: Sequence[str]) -> bool:
    """True for a band that is one cell across the whole table ("Technical specification")."""
    return len(set(boxes)) == 1 and bool(texts[0])


def is_header_band(texts: Sequence[str], max_words: int) -> bool:
    """True if a band reads as column labels: two or more short cells, none a number.

    A numbered row ("1. | Tender Reference No. | PRMM267035") or a clause is data, so a
    grid that starts with one has no header row.

    Args:
        texts: Cell texts of the band.
        max_words: Longest label, in words.

    Returns:
        Whether the band is the header.
    """
    filled = [t for t in texts if t]
    return len(filled) >= KEY_VALUE_COLUMNS and all(
        len(t.split()) <= max_words and not _ITEM_ID.match(t) and not _NUMERIC.match(t)
        for t in filled
    )


def rule_grid_table(
    words: PageWords,
    grid: RuleGrid,
    page_number: int,
    index: int,
    key_value: bool,
    header_max_words: int,
    paragraph_gap: float = 0.0,
) -> ExtractedTable | None:
    """Read one rule grid (see `layout`) as a table: title band, header band, body rows.

    Merged cells come back as one box per band they span, so their text is repeated in every
    row, like `fill_merged_cells` does for PyMuPDF grids.

    Args:
        words: The page's words.
        grid: A grid rebuilt from drawn rules.
        page_number: 1-based page number.
        index: Order of the table on its page.
        key_value: Read a two-column grid as label/value pairs.
        header_max_words: Longest header label, in words (see `is_header_band`).
        paragraph_gap: Paragraph-break gap for `cell_lines`; 0 = no breaks.

    Returns:
        The table, or None if it has no body rows.
    """
    bands = [
        (boxes, _band_texts(words, boxes), band_box(grid, i))
        for i, boxes in enumerate(grid_cells(grid))
    ]
    bands = [(boxes, texts, box) for boxes, texts, box in bands if any(texts)]
    title = None
    if bands and _is_title_band(bands[0][0], bands[0][1]):
        title, bands = bands[0][1][0], bands[1:]
    width = len(grid.xs) - 1
    if key_value or not bands:
        header = [""] * width
    elif is_header_band(bands[0][1], header_max_words):
        header, bands = header_names(bands[0][1]), bands[1:]
    else:  # the first band is already data (a numbered row, a clause)
        header = [f"Column {i + 1}" for i in range(width)]
    rows = [
        TableRow(
            cells=texts,
            bbox=box,
            cell_lines=cell_lines(words, boxes, paragraph_gap),
            cell_line_ys=cell_line_ys(words, boxes, paragraph_gap),
        )
        for boxes, texts, box in bands
    ]
    rows = [r for r in rows if not repeats_header(header, r.cells)]
    if not rows:
        return None
    return ExtractedTable(
        page_number=page_number,
        index=index,
        bbox=grid.bbox,
        header=header,
        rows=rows,
        key_value=key_value,
        title=title,
        from_rules=True,
    )


def rule_grid_tables(
    page: pymupdf.Page,
    key_value_pairs: bool,
    layout: LayoutOptions,
    paragraph_gap: float = 0.0,
    glyphs: RepairOptions | None = None,
) -> list[ExtractedTable]:
    """Tables rebuilt from drawn rules, for pages where PyMuPDF finds none (failure case T1).

    Args:
        page: A PyMuPDF page with a text layer.
        key_value_pairs: Read two-column grids as label/value pairs (tender files).
        layout: Rule-grid thresholds.
        paragraph_gap: Paragraph-break gap for `cell_lines`; 0 = no breaks.
        glyphs: Shifted-font repair options; None = words as extracted.

    Returns:
        The tables, top to bottom.
    """
    grids = page_grids(drawn_rules(page, layout.rule_max_thickness), layout)
    if not grids:
        return []
    words = PageWords.of(page, glyphs)
    tables: list[ExtractedTable] = []
    for grid in grids:
        key_value = key_value_pairs and len(grid.xs) - 1 == KEY_VALUE_COLUMNS
        table = rule_grid_table(
            words,
            grid,
            page.number + 1,
            len(tables),
            key_value,
            layout.header_max_words,
            paragraph_gap,
        )
        if table is not None:
            tables.append(table)
    return tables


def extract_page_tables(
    page: pymupdf.Page,
    key_value_pairs: bool = False,
    split_stacked: bool = False,
    cleanup: TableCleanupOptions | None = None,
    layout: LayoutOptions | None = None,
) -> list[ExtractedTable]:
    """Find the tables on one page.

    Args:
        page: A PyMuPDF page with a text layer.
        key_value_pairs: Read two-column tables as label/value pairs (tender files).
        split_stacked: Split rows holding several stacked items without a dividing line.
        cleanup: Row and header repairs (unit spellings, caption detection); None = off.
        layout: Rule-grid thresholds; with them, a page where PyMuPDF finds no table is read
            from its drawn rules. None = off.

    Returns:
        Tables in PyMuPDF's detection order, each with header and filled body rows. Tables
        with no body rows are dropped.
    """
    repair = cleanup or TableCleanupOptions()
    tables: list[ExtractedTable] = []
    found = page.find_tables().tables
    if not found and layout is not None:
        grids = rule_grid_tables(page, key_value_pairs, layout, repair.paragraph_gap, repair.glyphs)
        return [_repaired(t, repair) for t in grids]
    words = PageWords.of(page, repair.glyphs) if found else PageWords(words=())
    for table in found:
        grid_rows = _grid_rows(words, table, split_stacked)
        boxes = [row.box for row in grid_rows]
        filled = fill_merged_cells([row.cells for row in grid_rows], boxes)
        rows = [
            TableRow(
                cells=cells,
                bbox=row.box,
                stacked_unresolved=row.stacked_unresolved,
                cell_lines=cell_lines(
                    words, [c.bbox if c else None for c in row.cells], repair.paragraph_gap
                ),
                cell_line_ys=cell_line_ys(
                    words, [c.bbox if c else None for c in row.cells], repair.paragraph_gap
                ),
            )
            for cells, row in zip(filled, grid_rows, strict=True)
            if any(cells)
        ]
        key_value = key_value_pairs and table.col_count == KEY_VALUE_COLUMNS
        header, title, rows = _header_and_rows(
            words, table, rows, key_value, repair.max_label_words
        )
        rows = [merge_split_values(r, repair.units) for r in merge_continuation_rows(rows)]
        if not rows:
            continue
        tables.append(
            ExtractedTable(
                page_number=page.number + 1,
                index=len(tables),
                bbox=_to_bbox(table.bbox),
                header=header,
                rows=rows,
                key_value=key_value,
                title=title,
            )
        )
    return tables


def _repaired(table: ExtractedTable, repair: TableCleanupOptions) -> ExtractedTable:
    """Apply the row repairs (continuation lines, split values) to a rule-grid table."""
    rows = [merge_split_values(r, repair.units) for r in merge_continuation_rows(table.rows)]
    return table.model_copy(update={"rows": rows})


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
