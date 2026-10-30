"""Catalogue chunks -> labelled fields: the unit a comparator reads a value from.

Like-for-like (CLAUDE.md section 7) needs the label and the value on the same table row or
printed line, so a chunk is cut into fields:
  table row  one field per shown cell, labelled by the row's key cell and the column header
             ("Body" + "Materials WCB/ WCC" -> "A 216 Gr. WCB/ WCC")
  prose      one field per "Label: value" line group (wrapped lines continue the value), and
             one unlabelled field per other line ("Size range 80 to 2500 mm")
Only columns printed in the chunk's text are used: a version chunk shows one version column.
Rows of a dimension table are flagged; their numbers never decide a requirement.
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


@dataclass(frozen=True, slots=True)
class FieldOptions:
    """Field extraction thresholds (from settings)."""

    label_max_words: int
    dimension_words: tuple[str, ...]
    dimension_min_letter_columns: int

    @classmethod
    def from_settings(cls, settings: Settings) -> FieldOptions:
        """Build options from application settings."""
        return cls(
            label_max_words=settings.TENDER_LABEL_MAX_WORDS,
            dimension_words=tuple(settings.COMPARATOR_DIMENSION_WORDS),
            dimension_min_letter_columns=settings.COMPARATOR_DIMENSION_MIN_LETTER_COLUMNS,
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
    out: list[CatalogueField] = []
    for i in shown:
        own = _pair(header[i], cells[i])
        if key is None or key == i:
            out.append(CatalogueField(header[i], cells[i], own, (cells[i],), dimension))
            continue
        quote = _span(chunk.raw_text, _pair(header[key], cells[key]), own)
        label = f"{cells[key]} {header[i]}"
        out.append(CatalogueField(label, cells[i], quote, (cells[key], cells[i]), dimension))
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


def located_fields(chunks: Sequence[CatalogueChunk], options: FieldOptions) -> list[LocatedField]:
    """Fields of every chunk, with their source; computed once per manufacturer and item."""
    return [LocatedField(c, f) for c in chunks for f in chunk_fields(c.chunk, options)]
