"""Repair table rows that the PDF layout broke apart (pure functions on extracted rows).

Continuation lines (failure case V4): a cell whose text wraps onto a second line is often
read as a new row, e.g. "Cast Iron/ Bronze" then "/Stainless Steel", or "B class (upto 7.5
HP)" then "/ F Class(above 7.5 HP)". As separate rows the option is detached from its
attribute ("Impeller") and from its series column. Such a row is merged back into the row
above, cell by cell, so every column keeps its own value.

Split values (failure case V5): a value printed once across two columns ("Up to 16 bar"
centred over "Etanorm | Etanorm R") is cut by the column line into "Up to 16" | "bar", so one
column loses its unit and the other holds only "bar". The halves are joined again and the
whole value is given to every column it spans.

Captions (failure cases V2, V3): a table title read as the header row ("PERFORMANCE CHART
FOR | 'SRF' SERIES, | 2 POLE, ...") hides the real column labels, and a caption over blank
cells pushes the series names ("KDS | KDI | KDT") into the body as data. The caption is taken
out (it becomes the table's title, used as the section of its rows) and the row of real
labels becomes the header.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import TableRow
from app.schemas.parsing import BBox

# A wrapped option or alternative: "/Stainless Steel", "/ F Class".
_CONTINUATION_MARK: Final = re.compile(r"^/")
# A sentence or phrase that goes on: "or equivalent", "with gear unit". At least two letters,
# so single-letter dimension symbols ("d", "a") in dimension tables never count.
_LOWERCASE_START: Final = re.compile(r"^[a-z][a-z]")
# An ordering-code option ("2 - Two-piece", "G - Gear Unit") is its own row in a legend.
_OPTION_CODE: Final = re.compile(r"^[A-Za-z0-9]{1,2}\s*-\s*\S")
# The tail of a wrapped value: a bracketed note "(0.5 to 30 HP)", an option "/ F Class", or a
# lower-case word "with graphite filler".
_TAIL_START: Final = re.compile(r"^[(/a-z]")
# A value cut mid-phrase: "... CF8M to A 351,", "... Type 410 /", "... Graphite moulded rings with".
# A blank header cell, named by the extractor.
_PLACEHOLDER: Final = re.compile(r"^Column \d+$")
_MIN_HEADER_LABELS: Final = 2  # one label alone is a value, not a header row
# Series names under one caption ('KDS | KDI | KDT | KS | SRF'); two words could be a data row.
_MIN_SERIES_LABELS: Final = 3
# Split values: "Up to 16" | "bar" and "-30 °C to" | "+140 °C".
_ENDS_WITH_NUMBER: Final = re.compile(r"\d\s*$")
_OPEN_RANGE: Final = re.compile(r"\bto\s*$", re.IGNORECASE)
_STARTS_WITH_VALUE: Final = re.compile(r"^[+-]?\s*\d")  # matched on folded text (dashes -> "-")
_OPEN_END: Final = re.compile(r"(?:[,/&+-]|\b(?:with|to|and|or|of|for|in))$", re.IGNORECASE)


def _first_text(cells: Sequence[str]) -> str:
    """The first non-empty cell, stripped ("" if the row is empty)."""
    return next((c.strip() for c in cells if c.strip()), "")


def _cell(cells: Sequence[str], i: int) -> str:
    return cells[i].strip() if i < len(cells) else ""


def _wrapped_tail(above: str, cell: str) -> bool:
    """True if `cell` reads as the rest of `above`, not as a value of its own.

    A bare number ("600") or an option code ("2 - Two-piece") starts a row of its own, and so
    does text under an empty cell. Otherwise the tail must look wrapped (see `_TAIL_START`)
    or `above` must stop mid-phrase (see `_OPEN_END`): "Malleable Iron" then "SS 304" in a
    parts list are two parts (the second has no item number), not one value.
    """
    if not above or not any(ch.isalpha() for ch in cell) or _OPTION_CODE.match(cell):
        return False
    return bool(_TAIL_START.match(cell) or _OPEN_END.search(above))


def _wraps_into(above: Sequence[str], cells: Sequence[str]) -> bool:
    """True if every non-empty cell of the row is a wrapped tail of the cell above it."""
    filled = [i for i in range(len(cells)) if _cell(cells, i)]
    return all(_wrapped_tail(_cell(above, i), _cell(cells, i)) for i in filled)


def is_continuation_row(above: Sequence[str], cells: Sequence[str]) -> bool:
    """True if the row continues the row above instead of starting a new one.

    A continuation either starts with "/" or with a lower-case word ("or ...", "with ..."),
    or has an empty label (first) cell under a labelled row and only holds wrapped text
    (see `_wraps_into`). The guards keep separate rows apart in clean catalogues: class rows
    with an empty label ("600", "900") and ordering-code legends.

    Args:
        above: Cells of the row above.
        cells: The row's cell values.

    Returns:
        Whether the row should be merged into the row above.
    """
    first = _first_text(cells)
    if not first:
        return False
    if _CONTINUATION_MARK.match(first) or _LOWERCASE_START.match(first):
        return True
    return not _cell(cells, 0) and bool(_cell(above, 0)) and _wraps_into(above, cells)


def _join(above: str, below: str) -> str:
    """Append a continuation cell to the cell above, one space apart."""
    return f"{above} {below}".strip() if above and below else above or below


def _union(a: BBox, b: BBox) -> BBox:
    return BBox(x0=min(a.x0, b.x0), y0=min(a.y0, b.y0), x1=max(a.x1, b.x1), y1=max(a.y1, b.y1))


def merge_rows(above: TableRow, below: TableRow) -> TableRow:
    """Merge `below` into `above` cell by cell; the box grows to cover both.

    Args:
        above: The row the continuation belongs to.
        below: The continuation row.

    Returns:
        One row whose cells hold both lines.
    """
    width = max(len(above.cells), len(below.cells))
    top = [*above.cells, *[""] * (width - len(above.cells))]
    bottom = [*below.cells, *[""] * (width - len(below.cells))]
    return TableRow(
        cells=[_join(a, b) for a, b in zip(top, bottom, strict=True)],
        bbox=_union(above.bbox, below.bbox),
        stacked_unresolved=above.stacked_unresolved or below.stacked_unresolved,
    )


def merge_continuation_rows(rows: Sequence[TableRow]) -> list[TableRow]:
    """Merge every continuation row (see `is_continuation_row`) into the row above it.

    The first row is never merged: there is nothing above it in the body (the header is
    separate). Several continuation lines in a row all join the same row.

    Args:
        rows: Body rows of one table, top to bottom.

    Returns:
        The rows with continuations folded in.
    """
    merged: list[TableRow] = []
    for row in rows:
        if merged and is_continuation_row(merged[-1].cells, row.cells):
            merged[-1] = merge_rows(merged[-1], row)
        else:
            merged.append(row)
    return merged


def _ends_with_number(text: str) -> bool:
    return bool(_ENDS_WITH_NUMBER.search(text))


def is_unit_only(cell: str, units: frozenset[str]) -> bool:
    """True if the cell holds nothing but a unit spelling from the ontology ("bar", "°C")."""
    return fold_text(cell).strip() in units


def is_split_value(left: str, right: str, units: frozenset[str]) -> bool:
    """True if two neighbouring cells are the two halves of one value cut by a column line.

    "Up to 16" | "bar" (a number, then a cell that is only a unit) and "-30 °C to" |
    "+140 °C" (an open range, then the value that closes it).

    Args:
        left: The left cell.
        right: The right cell.
        units: Folded unit spellings (see `SearchVocabulary.unit_spellings`).

    Returns:
        Whether the cells should be joined.
    """
    left, right = left.strip(), right.strip()
    if not left or not right:
        return False
    if is_unit_only(right, units):
        return _ends_with_number(left)
    return bool(_OPEN_RANGE.search(left) and _STARTS_WITH_VALUE.match(fold_text(right)))


def merge_split_values(row: TableRow, units: frozenset[str]) -> TableRow:
    """Join values cut across columns; the joined value goes into every column it spans.

    The label (first) column is never joined: a label next to a unit column ("Pressure" |
    "bar") is not a split value.

    Args:
        row: One table row.
        units: Folded unit spellings.

    Returns:
        The row with spanning values restored, and `spanning_columns` set when any was.
    """
    cells = list(row.cells)
    spanning = set(row.spanning_columns)
    for i in range(1, len(cells) - 1):
        if is_split_value(cells[i], cells[i + 1], units):
            joined = f"{cells[i].strip()} {cells[i + 1].strip()}"
            cells[i] = cells[i + 1] = joined
            spanning |= {i, i + 1}
    if not spanning - set(row.spanning_columns):
        return row
    return row.model_copy(update={"cells": cells, "spanning_columns": tuple(sorted(spanning))})


@dataclass(frozen=True, slots=True)
class CaptionHeader:
    """A table header after caption detection: the caption (if any), header and body rows."""

    header: list[str]
    caption: str | None
    rows: list[TableRow]


@dataclass(frozen=True, slots=True)
class TableCleanupOptions:
    """Inputs of the row and header repairs (built once from settings and the ontology)."""

    units: frozenset[str] = frozenset()  # folded unit spellings; empty = no split-value join
    max_label_words: int = 0  # longest column label in words; 0 = no caption detection


def _word_count(text: str) -> int:
    return len(text.split())


def caption_text(header: Sequence[str]) -> str:
    """The header's real labels joined in order, spanned repeats and `Column N` dropped."""
    labels = [h for h in header if not _PLACEHOLDER.match(h)]
    distinct = [h for i, h in enumerate(labels) if i == 0 or h != labels[i - 1]]
    return " ".join(distinct)


def is_caption_header(header: Sequence[str], max_label_words: int) -> bool:
    """True if the header row is a title sentence spread over cells, not column labels.

    Column labels are short ("Power kW", "Pipe Size (mm)"); a caption such as "PERFORMANCE
    CHART FOR | 'SRF' SERIES, | 2 POLE, | MONOBLOC PUMPS, AT RATED VOLTAGE, 50 Hz ..." has a
    cell longer than any label, written as comma-separated clauses (failure case V2). The
    comma keeps long letter-spaced labels ("C L A S S 8 0 0 G A T E ...") out.

    Args:
        header: Header labels (blank cells named "Column N").
        max_label_words: Longest label, in words, that is still a column label.

    Returns:
        Whether the header is a caption.
    """
    return any(_word_count(h) > max_label_words and "," in h for h in header)


def is_label_row(cells: Sequence[str], max_label_words: int) -> bool:
    """True if every non-empty cell is a short label without numbers ("KDS", "KDI", ...).

    At least two labels are needed: one word alone is a value, not a header row.
    """
    filled = [c.strip() for c in cells if c.strip()]
    return len(filled) >= _MIN_HEADER_LABELS and all(
        _word_count(c) <= max_label_words and not any(ch.isdigit() for ch in c) for c in filled
    )


def _single_label(header: Sequence[str]) -> bool:
    """True if the header names one thing only (spanned repeats aside), the rest blank."""
    labels = {h for h in header if not _PLACEHOLDER.match(h)}
    return len(labels) == 1 and any(_PLACEHOLDER.match(h) for h in header)


def _names_series(header: Sequence[str], row: Sequence[str]) -> bool:
    """True if the row names every blank header column, with at least three names."""
    blanks = [i for i, h in enumerate(header) if _PLACEHOLDER.match(h)]
    names = [c for c in row if c.strip()]
    return len(names) >= _MIN_SERIES_LABELS and all(row[i].strip() for i in blanks)


def _header_from_row(header: Sequence[str], row: Sequence[str]) -> list[str]:
    """Labels from the row; blank cells keep their old name ("Column N" or the caption)."""
    return [r.strip() or h for h, r in zip(header, row, strict=True)]


def split_caption_header(
    header: Sequence[str], rows: Sequence[TableRow], max_label_words: int
) -> CaptionHeader:
    """Move a caption out of the header and use the row of real labels below it.

    Two layouts (failure cases V2, V3):
    - a title sentence read as the header ("PERFORMANCE CHART FOR ..."): the sentence is the
      caption; the first row becomes the header only if it is a row of labels;
    - one caption over blank cells ("TECHNICAL SPECIFICATION | Column 3 | ...") with the
      series names as the first body row ("| KDS | KDI | KDT"): the caption stays the name
      of the label column and the series names become the column headers.

    Args:
        header: Header labels (blank cells named "Column N").
        rows: Body rows.
        max_label_words: Longest label, in words, that is still a column label.

    Returns:
        The header, the caption (None if the header was already real labels) and the rows.
    """
    first = rows[0].cells if rows else []
    has_labels = len(first) == len(header) and is_label_row(first, max_label_words)
    if is_caption_header(header, max_label_words):
        caption = caption_text(header)
        if has_labels:
            return CaptionHeader(_header_from_row(_blank(header), first), caption, list(rows[1:]))
        return CaptionHeader(_blank(header), caption, list(rows))
    if _single_label(header) and has_labels and _names_series(header, first):
        return CaptionHeader(_header_from_row(header, first), caption_text(header), list(rows[1:]))
    return CaptionHeader(list(header), None, list(rows))


def _blank(header: Sequence[str]) -> list[str]:
    """Placeholder names for every column ("Column 1", "Column 2", ...)."""
    return [f"Column {i + 1}" for i in range(len(header))]
