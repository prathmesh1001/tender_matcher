"""Rebuild table rows on scanned tender pages from OCR word boxes (CLAUDE.md C5, A7).

Tesseract reads a scanned table either column by column (all labels, then all values) or with a
label and its value merged into one line. Both lose the row: "Rated Capacity at duty Point"
and "1500 lpm" end up far apart, or "Casing CAST IRON IS 210" has no separator. Here the words
are put back on rows:

1. Row bands: words that overlap vertically form one printed line. A band never grows taller
   than a row; a word taller than that spans rows (a merged serial-number column) and is noise.
2. Cells: inside a band, a gap much wider than the word height separates two cells.
3. Tables: a run of bands with two or more cells, ended by a full-width prose line.
4. Columns: cell left edges cluster; the first column is a serial column when its cells are
   mostly short ("1", "2.", "00"), and is dropped. A band with nothing in the serial or label
   column continues the row above (a value wrapped over lines, a list of spares).

Rows come out as `label | value` tables, the same shape as a vector GeM bid table, so the
tender parser reads them unchanged. A token is repaired only when an OCR-confusion variant of
it is exactly an ontology code ("55304" -> "SS304"); every repair is logged.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product
from statistics import median
from typing import Final

from app.core.config import Settings
from app.schemas.chunking import ExtractedTable, TableRow
from app.schemas.parsing import BBox, TextBlock

logger = logging.getLogger(__name__)

_ALNUM: Final = re.compile(r"[^\W_]", re.UNICODE)
_TOKEN_EDGE: Final = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)
_MAX_CONFUSABLE: Final = 6
_SERIAL_MARK: Final = re.compile(r"[\d|]")
_SERIAL_PREFIX_WORDS: Final = 3
_DIGIT: Final = re.compile(r"\d")
_VALUE_QUANTILE: Final = 4  # values start at the lower quartile of second-cell edges


@dataclass(frozen=True, slots=True)
class OcrTableOptions:
    """Thresholds for rebuilding scanned tables, plus the code rules used for repairs."""

    cell_gap_factor: float
    max_row_height_factor: float
    min_rows: int
    serial_max_chars: int
    prose_width_fraction: float
    max_wide_share: float = 0.25
    min_labelled_share: float = 0.6
    code_rules: tuple[tuple[re.Pattern[str], str], ...] = ()
    confusions: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_settings(
        cls, settings: Settings, code_rules: tuple[tuple[re.Pattern[str], str], ...] = ()
    ) -> OcrTableOptions:
        """Build options from settings and the ontology code rules."""
        return cls(
            cell_gap_factor=settings.OCR_TABLE_CELL_GAP_FACTOR,
            max_row_height_factor=settings.OCR_TABLE_MAX_ROW_HEIGHT_FACTOR,
            min_rows=settings.OCR_TABLE_MIN_ROWS,
            serial_max_chars=settings.OCR_TABLE_SERIAL_MAX_CHARS,
            prose_width_fraction=settings.OCR_TABLE_PROSE_WIDTH_FRACTION,
            max_wide_share=settings.OCR_TABLE_MAX_WIDE_SHARE,
            min_labelled_share=settings.OCR_TABLE_MIN_LABELLED_SHARE,
            code_rules=code_rules,
            confusions=dict(settings.OCR_CODE_CONFUSIONS),
        )


@dataclass(slots=True)
class _Band:
    """Words printed on one line, left to right once closed."""

    words: list[TextBlock]
    y0: float
    y1: float


@dataclass(frozen=True, slots=True)
class _Cell:
    """Words of one band separated from the next by a wide gap."""

    words: tuple[TextBlock, ...]

    @property
    def x0(self) -> float:
        return self.words[0].bbox.x0

    @property
    def x1(self) -> float:
        return max(w.bbox.x1 for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def is_short(text: str, max_chars: int) -> bool:
    """True for a serial number or OCR noise: at most `max_chars` letters and digits.

    Args:
        text: A cell's text.
        max_chars: The limit.

    Returns:
        Whether the text is short.
    """
    return len(_ALNUM.findall(text)) <= max_chars


def row_bands(words: Sequence[TextBlock], max_height_factor: float) -> list[list[TextBlock]]:
    """Group words into printed lines by vertical overlap.

    Args:
        words: A page's OCR words (any order).
        max_height_factor: A band stays below this many median word heights; taller words
            span several rows and are dropped.

    Returns:
        Bands top to bottom, each sorted left to right.
    """
    kept = [w for w in words if w.text.strip()]
    if not kept:
        return []
    limit = max_height_factor * median(w.bbox.y1 - w.bbox.y0 for w in kept)
    bands: list[_Band] = []
    for word in sorted(kept, key=lambda w: (w.bbox.y0, w.bbox.x0)):
        if word.bbox.y1 - word.bbox.y0 > limit:
            continue
        last = bands[-1] if bands else None
        if last and word.bbox.y0 < last.y1 and max(last.y1, word.bbox.y1) - last.y0 <= limit:
            if not any(_same_word(word, kept_word) for kept_word in last.words):
                last.words.append(word)
            last.y1 = max(last.y1, word.bbox.y1)
        else:
            bands.append(_Band([word], word.bbox.y0, word.bbox.y1))
    return [sorted(b.words, key=lambda w: w.bbox.x0) for b in bands]


def _same_word(a: TextBlock, b: TextBlock) -> bool:
    """True if `a` repeats `b`: same text over more than half of the narrower box."""
    if a.text != b.text:
        return False
    overlap = min(a.bbox.x1, b.bbox.x1) - max(a.bbox.x0, b.bbox.x0)
    narrower = min(a.bbox.x1 - a.bbox.x0, b.bbox.x1 - b.bbox.x0)
    return narrower > 0 and overlap > narrower / 2


def split_cells(band: Sequence[TextBlock], gap: float) -> list[_Cell]:
    """Split one printed line into cells at gaps wider than `gap`.

    Args:
        band: Words of the line, left to right.
        gap: Smallest gap that separates two cells.

    Returns:
        The cells, left to right.
    """
    cells: list[list[TextBlock]] = []
    for word in band:
        if cells and word.bbox.x0 - max(w.bbox.x1 for w in cells[-1]) <= gap:
            cells[-1].append(word)
        else:
            cells.append([word])
    return [_Cell(tuple(c)) for c in cells]


def _is_prose(cells: Sequence[_Cell], text_width: float, fraction: float) -> bool:
    """A single cell running across most of the page: a sentence, not a table row."""
    return len(cells) == 1 and cells[0].x1 - cells[0].x0 > fraction * text_width


def table_runs(
    lines: Sequence[list[_Cell]], text_width: float, options: OcrTableOptions
) -> list[tuple[int, int]]:
    """Find runs of lines that form a table.

    Args:
        lines: Cells of each printed line, top to bottom.
        text_width: Width of the page's text block.
        options: Thresholds.

    Returns:
        `(first, last)` line indexes (inclusive) of each table, from its first to its last
        line with two or more cells.
    """
    runs: list[tuple[int, int]] = []
    paired: list[int] = []
    for i, cells in enumerate([*lines, []]):
        prose = i == len(lines) or _is_prose(cells, text_width, options.prose_width_fraction)
        if prose:
            if len(paired) >= options.min_rows:
                runs.append((paired[0], paired[-1]))
            paired = []
        elif len(cells) >= 2:
            paired.append(i)
    return runs


def is_serial_word(text: str, short: int) -> bool:
    """True for a serial number or a misread table rule: "7.", "10", "|", "S|", "|_1."."""
    return is_short(text, short) and (bool(_SERIAL_MARK.search(text)) or not _ALNUM.search(text))


def serial_prefix(words: Sequence[TextBlock], short: int) -> int:
    """How many leading words form a serial number ("7. |", "| 6. |", "ie 1. |").

    Args:
        words: A cell's words, left to right.
        short: Most letters and digits of a serial word.

    Returns:
        Count of words to drop: up to the last serial word among the first few short words.
    """
    end = 0
    for i, word in enumerate(words[:_SERIAL_PREFIX_WORDS]):
        if not is_short(word.text, short):
            break
        if is_serial_word(word.text, short):
            end = i + 1
    return end


def _without_serial_cells(
    cells: Sequence[_Cell], short: int, split_x: float | None = None
) -> list[_Cell]:
    """The line's cells after leading short cells of a serial column ("1", "if", "00").

    A short cell is a serial number only when a label cell follows it: before the value
    column, or (value column not known yet) with a value cell after that. "RPM | 2900 RPM"
    keeps its label.
    """
    kept = list(cells)
    while len(kept) > 1 and is_short(kept[0].text, short):
        label_follows = kept[1].x0 < split_x if split_x is not None else len(kept) > 2
        if not label_follows:
            break
        kept.pop(0)
    return kept


def value_start(lines: Sequence[list[_Cell]], short: int) -> float | None:
    """Left edge of the value column: a low quantile of where second cells start.

    Leading serial cells are skipped first, so a separate serial column does not make the
    label the "second" cell.

    Args:
        lines: Cells of a table's printed lines.
        short: Most letters and digits of a serial cell.

    Returns:
        The x where values begin, or None if no line has two cells.
    """
    trimmed = [_without_serial_cells(cells, short) for cells in lines]
    seconds = sorted(cells[1].x0 for cells in trimmed if len(cells) >= 2)
    return seconds[len(seconds) // _VALUE_QUANTILE] if seconds else None


def is_two_column(lines: Sequence[list[_Cell]], options: OcrTableOptions) -> bool:
    """True if the table is label | value: few lines have three or more cells.

    Wider scanned tables (description | size | quantity; parameter | pump A | pump B) are not
    rebuilt: joining their columns into one value would mix two items' values (CLAUDE.md B10),
    so they keep the OCR reading.

    Args:
        lines: Cells of the table's printed lines.
        options: Thresholds.

    Returns:
        Whether the share of wide lines, among lines with two or more cells, is small enough.
    """
    trimmed = [_without_serial_cells(c, options.serial_max_chars) for c in lines]
    paired = [c for c in trimmed if len(c) >= 2]
    wide = sum(len(c) >= 3 for c in paired)
    return bool(paired) and wide / len(paired) <= options.max_wide_share


def mostly_labelled(rows: Sequence[TableRow], options: OcrTableOptions) -> bool:
    """True if enough rebuilt rows have a label, as a label | value table does.

    A list whose middle columns hold short cells (sizes "3/4", quantities) loses them as serial
    numbers and leaves rows with no label; such a table keeps the OCR reading.
    """
    return sum(bool(r.cells[0]) for r in rows) / len(rows) >= options.min_labelled_share


@dataclass(slots=True)
class _Row:
    """Label and value lines of one table row."""

    label: list[list[TextBlock]] = field(default_factory=list)
    value: list[list[TextBlock]] = field(default_factory=list)
    trailing_value_line: bool = False


@dataclass(frozen=True, slots=True)
class _Line:
    """One printed line of a table split into its label part and value part."""

    label: list[TextBlock]
    value: list[TextBlock]
    numbered: bool


def split_line(cells: Sequence[_Cell], split_x: float, label_zone: float, short: int) -> _Line:
    """Split a line at the value column, dropping its serial number and stray table rules.

    Args:
        cells: The line's cells.
        split_x: Words starting left of this are label text.
        label_zone: A leftmost cell starting left of this may open with a serial number.
        short: Most letters and digits of a serial word.

    Returns:
        The label words, value words, and whether a serial number was dropped.
    """
    label: list[TextBlock] = []
    value: list[TextBlock] = []
    kept = _without_serial_cells(cells, short, split_x) if cells[0].x0 < label_zone else cells
    numbered = len(kept) < len(cells)
    for i, cell in enumerate(kept):
        words = list(cell.words)
        if i == 0 and cell.x0 < label_zone:
            cut = serial_prefix(words, short)
            numbered = numbered or any(_DIGIT.search(w.text) for w in words[:cut])
            words = words[cut:]
        while words and not _ALNUM.search(words[0].text):
            words.pop(0)
        if words:
            (label if words[0].bbox.x0 < split_x else value).extend(words)
    return _Line(label, value, numbered)


def build_rows(lines: Sequence[_Line]) -> list[_Row]:
    """Put a table's lines on rows.

    A line with label text or a serial number starts a row; a value-only line continues the row
    above (a wrapped value, a list of spares), unless the next row has no value of its own: then
    the line is that row's value printed centred over two lines, starting above its label.

    Args:
        lines: The table's lines, top to bottom.

    Returns:
        The rows.
    """
    rows: list[_Row] = []
    for line in lines:
        if line.label or line.numbered or not rows:
            row = _Row()
            previous = rows[-1] if rows else None
            if previous and previous.trailing_value_line and not line.value:
                row.value.append(previous.value.pop())
            rows.append(row)
            if line.label:
                row.label.append(line.label)
            if line.value:
                row.value.append(line.value)
            continue
        rows[-1].value.append(line.value)
        rows[-1].trailing_value_line = True
    return rows


def repair_codes(
    text: str,
    code_rules: Sequence[tuple[re.Pattern[str], str]],
    confusions: Mapping[str, str],
) -> tuple[str, list[str]]:
    """Repair OCR-confused characters in tokens that then read exactly as an ontology code.

    Only a token that is no code as read, and whose variant (each confusable character swapped
    for one of its look-alikes) is matched whole by a code rule, is changed. Numbers that are
    no code under any variant ("1500") are left alone.

    Args:
        text: A cell's text.
        code_rules: Compiled ontology code rules (lower-case text).
        confusions: Character -> look-alike characters ("5" -> "s").

    Returns:
        The text with repaired tokens upper-cased, and "old -> new" for each repair.
    """
    repairs: list[str] = []
    out: list[str] = []
    for token in re.split(r"(\s+|/)", text):
        lead, core, tail = _TOKEN_EDGE.match(token).groups()  # type: ignore[union-attr]
        fixed = _repaired(core, code_rules, confusions)
        if fixed is not None:
            repairs.append(f"{core} -> {fixed}")
            core = fixed
        out.append(f"{lead}{core}{tail}")
    return "".join(out), repairs


def _is_code(token: str, code_rules: Sequence[tuple[re.Pattern[str], str]]) -> bool:
    """True if a code rule matches the whole lower-cased token."""
    return any(
        (m := pattern.search(token)) is not None and m.group(0) == token
        for pattern, _ in code_rules
    )


def _repaired(
    token: str,
    code_rules: Sequence[tuple[re.Pattern[str], str]],
    confusions: Mapping[str, str],
) -> str | None:
    """The one variant of `token` that is a code, or None (no variant, or several)."""
    lower = token.lower()
    if not lower or not any(ch.isdigit() for ch in lower) or _is_code(lower, code_rules):
        return None
    options = [ch + confusions.get(ch, "") for ch in lower]
    if sum(len(o) > 1 for o in options) > _MAX_CONFUSABLE:
        return None
    found = {
        v
        for v in ("".join(chars) for chars in product(*options))
        if v != lower and _is_code(v, code_rules)
    }
    return found.pop().upper() if len(found) == 1 else None


def _union(words: Sequence[TextBlock]) -> BBox:
    """Box around the words."""
    return BBox(
        x0=min(w.bbox.x0 for w in words),
        y0=min(w.bbox.y0 for w in words),
        x1=max(w.bbox.x1 for w in words),
        y1=max(w.bbox.y1 for w in words),
    )


def _cell_lines(lines: Sequence[list[TextBlock]], options: OcrTableOptions, page: int) -> list[str]:
    """Text of each line of a cell, with OCR-confused codes repaired and logged."""
    out: list[str] = []
    for words in lines:
        text, repairs = repair_codes(
            " ".join(w.text for w in words), options.code_rules, options.confusions
        )
        for repair in repairs:
            logger.info("Repaired OCR code", extra={"page": page, "repair": repair})
        out.append(text)
    return out


def _table_row(row: _Row, options: OcrTableOptions, page: int) -> TableRow | None:
    """One rebuilt row as a `label | value` `TableRow`; None for a row with no text."""
    cell_lines = [_cell_lines(row.label, options, page), _cell_lines(row.value, options, page)]
    cells = [" ".join(lines) for lines in cell_lines]
    words = [w for line in [*row.label, *row.value] for w in line]
    if not words:
        return None
    confidences = [w.confidence for w in words if w.confidence is not None]
    return TableRow(
        cells=cells,
        bbox=_union(words),
        cell_lines=cell_lines if any(len(lines) > 1 for lines in cell_lines) else [],
        ocr_confidence=sum(confidences) / len(confidences) if confidences else None,
    )


def ocr_tables(
    words: Sequence[TextBlock], page_number: int, options: OcrTableOptions
) -> list[ExtractedTable]:
    """Rebuild the tables of one scanned page from its OCR words.

    Args:
        words: The page's OCR words in PDF points.
        page_number: 1-based page number.
        options: Thresholds and code rules.

    Returns:
        Label/value tables (`key_value=True`), top to bottom; empty when the page has none.
    """
    bands = row_bands(words, options.max_row_height_factor)
    if not bands:
        return []
    height = median(w.bbox.y1 - w.bbox.y0 for b in bands for w in b)
    gap = options.cell_gap_factor * height
    lines = [split_cells(b, gap) for b in bands]
    text_width = max(w.bbox.x1 for b in bands for w in b) - min(b[0].bbox.x0 for b in bands)
    tables: list[ExtractedTable] = []
    for first, last in table_runs(lines, text_width, options):
        run = lines[first : last + 1]
        split_x = value_start(run, options.serial_max_chars)
        if split_x is None or not is_two_column(run, options):
            continue
        left = median(cells[0].x0 for cells in run)
        split = [split_line(c, split_x - gap, left + gap, options.serial_max_chars) for c in run]
        built = [r for r in (_table_row(r, options, page_number) for r in build_rows(split)) if r]
        if len(built) < options.min_rows or not mostly_labelled(built, options):
            continue
        tables.append(
            ExtractedTable(
                page_number=page_number,
                index=len(tables),
                bbox=_union([w for b in bands[first : last + 1] for w in b]),
                header=[""] * len(built[0].cells),
                rows=built,
                key_value=True,
            )
        )
    return tables
