"""Catalogue chunks -> labelled fields: the unit a comparator reads a value from.

Like-for-like (CLAUDE.md section 7) needs the label and the value on the same table row or
printed line, so a chunk is cut into fields:
  table row  one field per shown cell, labelled by the row's key cell and the column header
             ("Body" + "Materials WCB/ WCC" -> "A 216 Gr. WCB/ WCC")
  prose      one field per "Label: value" line group (wrapped lines continue the value), and
             one unlabelled field per other line ("Size range 80 to 2500 mm")
Only columns printed in the chunk's text are used: a version chunk shows one version column.
A key cell can swallow the first column's values when the table has no key column of its own
("Pump Shaft" / "Carbon Steel" on separate lines of one cell): it is split into key and value.
Rows of a dimension table are flagged; their numbers never decide a requirement.
A cell the page prints interleaved with other columns (`interleaved_cells`, found at
ingestion: an ordering legend read as one row) is cited line by line, and a line keeps the
row key only if a key line is printed at its height, so a label never pairs with a value
from another row (like-for-like, CLAUDE.md section 7).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import Settings
from app.matching.chunk_source import CatalogueChunk
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.tender.normalizer import words
from app.tender.splitter import label_of

# Bullets and dashes that start a list line (the en dash is meant).
_BULLETS = "•·▪●○◦–-*"  # noqa: RUF001


@dataclass(frozen=True, slots=True)
class CatalogueField:
    """One label/value pair read from a chunk. All text is verbatim from `raw_text`."""

    label: str  # "" for an unlabelled line
    value: str
    quote: str  # a substring of the chunk's raw_text
    parts: tuple[str, ...]  # pieces printed on the page (cells or lines)
    in_dimension_table: bool = False
    key: str = ""  # a table row's key cell; the label adds the column header to it


@dataclass(frozen=True, slots=True)
class FieldOptions:
    """Field extraction thresholds (from settings)."""

    label_max_words: int
    dimension_words: tuple[str, ...]
    dimension_min_letter_columns: int
    option_sections: tuple[str, ...] = ()
    line_y_tolerance: float = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> FieldOptions:
        """Build options from application settings."""
        return cls(
            label_max_words=settings.TENDER_LABEL_MAX_WORDS,
            dimension_words=tuple(settings.COMPARATOR_DIMENSION_WORDS),
            dimension_min_letter_columns=settings.COMPARATOR_DIMENSION_MIN_LETTER_COLUMNS,
            option_sections=tuple(settings.CATALOGUE_OPTION_SECTIONS),
            line_y_tolerance=settings.COMPARATOR_LINE_Y_TOLERANCE,
        )


@dataclass(frozen=True, slots=True)
class LocatedField:
    """A field with the chunk (and document) it was read from."""

    source: CatalogueChunk
    field: CatalogueField


def is_dimension_table(header: Sequence[str], section: str | None, options: FieldOptions) -> bool:
    """True if a header or its section names dimensions, or has several one-letter columns."""
    for text in (*header, section or ""):
        key = f" {' '.join(words(text))} "
        if any(f" {' '.join(words(w))} " in key for w in options.dimension_words):
            return True
    letters = sum(1 for h in header if len(h.strip()) == 1 and h.strip().isalpha())
    return letters >= options.dimension_min_letter_columns


def _pair(header: str, cell: str) -> str:
    return f"{header}: {cell}"


def _span(raw: str, first: str, last: str) -> str:
    """The shortest substring of `raw` from `first` through `last` (both verbatim in it)."""
    start = raw.find(first)
    end = raw.find(last, start)
    if start < 0 or end < 0:
        return last
    return raw[start : end + len(last)]


def _row_key(cells: Sequence[str], shown: Sequence[int]) -> int | None:
    """The first shown cell holding a word: the row's key ("Body", "Wafer")."""
    return next((i for i in shown if any(ch.isalpha() for ch in cells[i])), None)


def merged_key(lines: Sequence[str], siblings: Sequence[str]) -> tuple[str, str] | None:
    """Split a key cell holding its row's first values: ("Pump Shaft", "Carbon Steel ...").

    The value starts at the first printed line, after the first, whose words also appear in
    another cell of the row. Line breaks and siblings are both needed: "Gate Valve" next to
    "Valve" is one line, and a wrapped key ("Wear" / "Ring") shares nothing with its row. A
    sibling equal to the cell is that cell spread over merged columns, so it proves nothing. The
    key must hold a word, and bare numbers never count as a value (B4).

    Args:
        lines: The cell's lines as printed.
        siblings: The row's other cells.

    Returns:
        The key and the value, or None if the cell is not merged.
    """
    whole = " ".join(words(" ".join(lines)))
    others = [f" {' '.join(words(s))} " for s in siblings if " ".join(words(s)) != whole]
    for n, line in enumerate(lines[1:], start=1):
        wanted = " ".join(words(line))
        if any(ch.isalpha() for ch in wanted) and any(f" {wanted} " in o for o in others):
            key = " ".join(lines[:n])
            return (key, " ".join(lines[n:])) if any(ch.isalpha() for ch in key) else None
    return None


def _key_parts(chunk: ChunkDraft, cells: Sequence[str], key: int | None) -> tuple[str, str]:
    """A row's key text and the value its key cell also holds ("" if the cell is only a key)."""
    if key is None:
        return "", ""
    if key != 0:  # only a table's first column can hold a key that has no column of its own
        return cells[key], ""
    lines: list[list[str]] = chunk.meta.get("cell_lines", [])
    siblings = [c for i, c in enumerate(cells) if i != key]
    split = merged_key(lines[key], siblings) if key < len(lines) else None
    if split is None or f"{split[0]} {split[1]}" != cells[key]:
        return cells[key], ""
    return split


@dataclass(frozen=True, slots=True)
class _RowLines:
    """A row's printed lines with their heights, and which cells are interleaved."""

    lines: list[list[str]]
    ys: list[list[float | None]]
    interleaved: frozenset[int]

    @classmethod
    def of(cls, chunk: ChunkDraft) -> _RowLines:
        """Read from chunk meta; heights are used only when every cell has them."""
        lines: list[list[str]] = chunk.meta.get("cell_lines", [])
        ys: list[list[float | None]] = chunk.meta.get("cell_line_ys", [])
        known = [len(c) for c in ys] == [len(c) for c in lines]
        cells = frozenset(chunk.meta.get("interleaved_cells", [])) if known and ys else frozenset()
        return cls(lines=lines, ys=ys if known else [], interleaved=cells)

    def printed(self, cell: int) -> list[tuple[float, str]]:
        """`(y, text)` of a cell's non-empty lines."""
        if cell >= len(self.ys):
            return []
        pairs = zip(self.ys[cell], self.lines[cell], strict=True)
        return [(y, t) for y, t in pairs if y is not None and t.strip()]


def _line_fields(
    rows: _RowLines, cell: int, key: int | None, header: str, dimension: bool, tolerance: float
) -> list[CatalogueField]:
    """One field per printed line of an interleaved cell (see module doc).

    Args:
        rows: The row's lines.
        cell: The interleaved cell.
        key: The row's key cell (None, or the cell itself: no key).
        header: The cell's column header.
        dimension: The row is in a dimension table.
        tolerance: Largest height difference of a key line printed beside a value line.

    Returns:
        The fields, top to bottom.
    """
    keys = rows.printed(key) if key is not None and key != cell else []
    out: list[CatalogueField] = []
    for y, line in rows.printed(cell):
        beside = " ".join(t for ky, t in keys if abs(ky - y) <= tolerance)
        if beside:
            label = f"{beside} {header}"
            out.append(CatalogueField(label, line, line, (beside, line), dimension, beside))
        else:
            out.append(CatalogueField(header, line, line, (line,), dimension))
    return out


def table_fields(chunk: ChunkDraft, options: FieldOptions) -> list[CatalogueField]:
    """Fields of one table-row chunk (see module doc).

    Args:
        chunk: A table-row chunk with `header` and `cells` meta.
        options: Field options.

    Returns:
        One field per shown non-empty cell; for a label/value row, one field.
    """
    cells: list[str] = [c.strip() for c in chunk.meta.get("cells", [])]
    header: list[str] = [h.strip() for h in chunk.meta.get("header", [])]
    if not header:
        if len(cells) < 2 or not cells[0] or not cells[1]:
            return []
        quote = _span(chunk.raw_text, cells[0], cells[1])
        return [CatalogueField(cells[0], cells[1], quote, (cells[0], cells[1]))]
    dimension = is_dimension_table(header, chunk.section, options)
    shown = [
        i
        for i, (h, c) in enumerate(zip(header, cells, strict=False))
        if c and _pair(h, c) in chunk.raw_text
    ]
    key = _row_key(cells, shown)
    rows = _RowLines.of(chunk)
    if rows.interleaved:
        return _interleaved_row_fields(cells, header, shown, key, rows, dimension, options)
    key_text, key_value = _key_parts(chunk, cells, key)
    # Cells wrap across columns on the page, so a split row cites its cells line by line.
    lines: list[list[str]] = chunk.meta.get("cell_lines", []) if key_value else []
    out: list[CatalogueField] = []
    for i in shown:
        own = _pair(header[i], cells[i])
        printed = tuple(lines[i]) if i < len(lines) and lines[i] else (cells[i],)
        if key_value and key == i:
            label = f"{key_text} {header[i]}"
            out.append(CatalogueField(label, key_value, own, printed, dimension, key_text))
            continue
        if key is None or key == i:
            out.append(CatalogueField(header[i], cells[i], own, (cells[i],), dimension))
            continue
        quote = _span(chunk.raw_text, _pair(header[key], cells[key]), own)
        label = f"{key_text} {header[i]}"
        parts = (key_text, *printed)
        out.append(CatalogueField(label, cells[i], quote, parts, dimension, key_text))
    return out


def _interleaved_row_fields(
    cells: Sequence[str],
    header: Sequence[str],
    shown: Sequence[int],
    key: int | None,
    rows: _RowLines,
    dimension: bool,
    options: FieldOptions,
) -> list[CatalogueField]:
    """Fields of a row holding interleaved cells: those cells line by line (see module doc).

    When the key is interleaved, it stacks several rows ("Disc" / "Stem"), so a cell with as
    many printed lines is read line by line too, each line beside its own key line. The other
    cells pair with the key as usual, citing the key's printed lines. A cell repeating an
    interleaved cell's text without lines of its own is that cell filled across merged
    columns, so it is skipped.
    """
    spread = {cells[i] for i in rows.interleaved if i < len(cells)}
    key_parts: tuple[str, ...] = ()
    if key is not None:
        key_parts = (
            tuple(t for _, t in rows.printed(key)) if key in rows.interleaved else (cells[key],)
        )
    stacked = key in rows.interleaved and key is not None
    out: list[CatalogueField] = []
    for i in shown:
        aligned = stacked and i != key and len(rows.printed(i)) == len(rows.printed(key or 0))
        if i in rows.interleaved or aligned:
            out += _line_fields(rows, i, key, header[i], dimension, options.line_y_tolerance)
        elif cells[i] in spread and not rows.printed(i):
            continue
        elif key is None or key == i:
            out.append(CatalogueField(header[i], cells[i], cells[i], (cells[i],), dimension))
        else:
            label = f"{cells[key]} {header[i]}"
            parts = (*key_parts, cells[i])
            out.append(CatalogueField(label, cells[i], cells[i], parts, dimension, cells[key]))
    return out


def _clean(line: str) -> str:
    return line.strip().lstrip(_BULLETS).strip()


def prose_fields(chunk: ChunkDraft, options: FieldOptions) -> list[CatalogueField]:
    """Fields of one prose chunk (see module doc).

    Args:
        chunk: A prose chunk.
        options: Field options (longest label).

    Returns:
        Labelled line groups and unlabelled lines, in reading order.
    """
    out: list[CatalogueField] = []
    group: tuple[str, list[str], list[str]] | None = None  # label, values, original lines

    def close() -> None:
        if group is not None:
            label, values, lines = group
            value = " ".join(v for v in values if v)
            out.append(
                CatalogueField(label, value, "\n".join(lines), tuple(_clean(x) for x in lines))
            )

    for line in chunk.raw_text.splitlines():
        text = _clean(line)
        if not text:
            close()
            group = None
            continue
        labelled = label_of(text, options.label_max_words)
        if labelled is not None:
            close()
            group = (labelled[0], [labelled[1]], [line])
            continue
        out.append(CatalogueField("", text, line, (text,)))
        if group is not None:
            group[1].append(text)
            group[2].append(line)
    close()
    return out


def chunk_fields(chunk: ChunkDraft, options: FieldOptions) -> list[CatalogueField]:
    """Fields of any chunk."""
    if chunk.chunk_type is ChunkType.TABLE_ROW:
        return table_fields(chunk, options)
    return prose_fields(chunk, options)


def _lists_options(chunk: ChunkDraft, options: FieldOptions) -> bool:
    """True for an ordering-code legend, or a chunk under a section about ordering."""
    if chunk.meta.get("option_legend"):
        return True
    section = f" {' '.join(words(chunk.section or ''))} "
    return any(f" {' '.join(words(s))} " in section for s in options.option_sections)


def option_fields(chunks: Sequence[CatalogueChunk], options: FieldOptions) -> list[LocatedField]:
    """Fields of the document's pages that list what can be ordered, dimension tables left out.

    The whole page counts: its lines ("LCB for ASTM A352 Gr. LCB") and tables ("Trim Materials")
    print under whatever text the layout took for a section.

    Args:
        chunks: One document's chunks.
        options: Field options.

    Returns:
        The fields, in the order given.
    """
    pages = {c.chunk.page_number for c in chunks if _lists_options(c.chunk, options)}
    on_pages = [c for c in chunks if c.chunk.page_number in pages]
    return [f for f in located_fields(on_pages, options) if not f.field.in_dimension_table]


def located_fields(chunks: Sequence[CatalogueChunk], options: FieldOptions) -> list[LocatedField]:
    """Fields of every chunk, with their source; computed once per manufacturer and item."""
    return [LocatedField(c, f) for c in chunks for f in chunk_fields(c.chunk, options)]
