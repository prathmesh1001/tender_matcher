"""Turn parsed pages and their tables into retrieval chunks.

Rules (CLAUDE.md section 6):
- Every chunk belongs to exactly one page, so citations are always a single page.
- Each table row becomes its own chunk, with the header and the model repeated, so a row like
  "120 | 32 | 7.5" is never separated from what its numbers mean.
- Other text becomes section-aware prose chunks of about `target_tokens`, with a small overlap.
  Text inside a table's box is not repeated as prose.

Prose chunk text is kept verbatim (it is quoted as evidence later); the section heading is
stored in `section`, not prepended to the text.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.core.config import Settings
from app.ingestion.boilerplate import in_bottom_band
from app.schemas.chunking import ChunkDraft, ExtractedTable, TableRow
from app.schemas.enums import ChunkType
from app.schemas.parsing import BBox, ParsedPage, TextBlock

_TOKEN: Final = re.compile(r"\w+|[^\w\s]")
_NUMBERED_HEADING: Final = re.compile(r"^\d+(\.\d+)*\.?\s+\S")
_KEY_VALUE: Final = re.compile(r":\s*\S")
_HAS_CONTENT: Final = re.compile(r"[^\W_]")
_REPLACEMENT_CHAR: Final = "\N{REPLACEMENT CHARACTER}"
# An ordering-code cell: a 1-2 character code, a dash, a label ("1 - PN 10", "G - Gear Unit").
_OPTION_CELL: Final = re.compile(r"^[A-Za-z0-9]{1,2}\s*-\s*\S")
_MIN_OPTION_COLUMNS: Final = 2  # one coded column is just a list, not an ordering legend


@dataclass(frozen=True, slots=True)
class ChunkerOptions:
    """Chunking thresholds (built from settings so the chunker never reads config)."""

    target_tokens: int
    max_tokens: int
    overlap_tokens: int
    heading_max_words: int
    heading_min_latin_ratio: float
    model_header_keywords: tuple[str, ...]
    variant_header_keywords: tuple[str, ...] = ()
    heading_max_digit_share: float = 1.0
    # Bottom share of the page whose headings end at the page break; 0 = off (DOCX).
    footer_band: float = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> ChunkerOptions:
        """Build options from application settings."""
        return cls(
            target_tokens=settings.CHUNK_TARGET_TOKENS,
            max_tokens=settings.CHUNK_MAX_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
            heading_max_words=settings.CHUNK_HEADING_MAX_WORDS,
            heading_min_latin_ratio=settings.CHUNK_HEADING_MIN_LATIN_RATIO,
            heading_max_digit_share=settings.CHUNK_HEADING_MAX_DIGIT_SHARE,
            footer_band=settings.PAGE_MARGIN_BAND_FRACTION,
            model_header_keywords=tuple(k.lower() for k in settings.CHUNK_MODEL_HEADER_KEYWORDS),
            variant_header_keywords=tuple(
                k.lower() for k in settings.CHUNK_VARIANT_HEADER_KEYWORDS
            ),
        )


def estimate_tokens(text: str) -> int:
    """Approximate the token count as words plus punctuation marks.

    Close enough to sub-word tokenizers for sizing chunks; it is not used for billing.

    Args:
        text: Any text.

    Returns:
        Estimated number of tokens.
    """
    return len(_TOKEN.findall(text))


def latin_ratio(text: str) -> float:
    """Share of letters in `text` that are ASCII (Latin) letters; 0.0 if it has no letters."""
    letters = [ch for ch in text if ch.isalpha()]
    return sum(ch.isascii() for ch in letters) / len(letters) if letters else 0.0


def digit_share(text: str) -> float:
    """Share of digits among the letters and digits of `text`; 0.0 if it has neither."""
    chars = [ch for ch in text if ch.isalnum()]
    return sum(ch.isdigit() for ch in chars) / len(chars) if chars else 0.0


def is_heading(
    text: str, max_words: int, min_latin_ratio: float = 0.0, max_digit_share: float = 1.0
) -> bool:
    """Guess whether a text block is a section heading.

    A heading is one short line that is either numbered ("3.2 Materials"), ALL CAPS, or Title
    Case, and is not a sentence (no final period) or a "key: value" spec line. Lines that are
    mostly non-Latin, or contain U+FFFD, are not headings: GeM bid PDFs extract their Hindi
    labels as broken glyphs, which must not become every following chunk's section. Lines that
    are mostly digits are document codes or file numbers ("3PHMP-05-2018-01"), not headings.

    Args:
        text: A text block.
        max_words: Longer blocks are never headings.
        min_latin_ratio: Minimum share of Latin letters among all letters.
        max_digit_share: Maximum share of digits among letters and digits.

    Returns:
        True if the block looks like a heading.
    """
    stripped = text.strip()
    words = stripped.split()
    if not words or "\n" in stripped or len(words) > max_words:
        return False
    if stripped.endswith(".") or _KEY_VALUE.search(stripped) or _REPLACEMENT_CHAR in stripped:
        return False
    if not any(ch.isalpha() for ch in stripped) or latin_ratio(stripped) < min_latin_ratio:
        return False
    if digit_share(stripped) > max_digit_share:
        return False
    if _NUMBERED_HEADING.match(stripped) or stripped.isupper():
        return True
    alpha_words = [w for w in words if w[0].isalpha()]
    return bool(alpha_words) and all(w[0].isupper() for w in alpha_words)


def has_content(text: str) -> bool:
    """True if the text has at least one letter or digit (punctuation-only chunks are noise)."""
    return bool(_HAS_CONTENT.search(text))


def find_model_column(header: Sequence[str], keywords: Sequence[str]) -> int | None:
    """Return the index of the first header that names the model/type column.

    Args:
        header: Table header labels.
        keywords: Lower-case words that identify a model column, e.g. "model".

    Returns:
        Column index, or None if no header matches.
    """
    for i, name in enumerate(header):
        label_words = set(re.findall(r"[a-z]+", name.lower()))
        if label_words & set(keywords):
            return i
    return None


def _first_word(label: str) -> str:
    """Lower-case first word of a header label ("" if it has none)."""
    words = re.findall(r"[a-z]+", label.lower())
    return words[0] if words else ""


def find_variant_columns(header: Sequence[str], keywords: Sequence[str]) -> list[int]:
    """Columns that each hold one version of the same attribute, e.g. one material grade each.

    Catalogue material tables print one column per body-material version ("Materials WCB/ WCC",
    "Materials LCB/ LCC", ...). Values from different columns must never be combined. Columns
    are versions when at least two headers start with the same keyword; "Body Material" and
    "Disc Material" start with different words, so they are different attributes, not versions.

    Args:
        header: Table header labels.
        keywords: Lower-case first words that mark a version column, e.g. "materials".

    Returns:
        Indexes of the first group of two or more version columns, or [] if there is none.
    """
    groups: dict[str, list[int]] = {}
    for i, label in enumerate(header):
        word = _first_word(label)
        if word in keywords:
            groups.setdefault(word, []).append(i)
    return next((cols for cols in groups.values() if len(cols) >= 2), [])


def distinct_columns(rows: Sequence[TableRow], columns: Sequence[int]) -> list[int]:
    """Drop version columns that repeat the previous one in every row.

    A header cell spanning two columns ("Material" over a merged value) looks like two
    versions with the same label; its columns hold the same text in every row, so it is one.

    Args:
        rows: Table body rows.
        columns: Candidate version columns, left to right.

    Returns:
        The columns that differ from their left neighbour in at least one row.
    """

    def cell(row: TableRow, i: int) -> str:
        return row.cells[i] if i < len(row.cells) else ""

    kept: list[int] = []
    for c in columns:
        if not kept or any(cell(r, c) != cell(r, kept[-1]) for r in rows):
            kept.append(c)
    return kept


def variant_name(header: Sequence[str], columns: Sequence[int], column: int) -> str:
    """Name of one version column: its header words that the other version columns lack.

    "Materials WCB/ WCC" among "Materials ..." columns -> "WCB/ WCC". Identical labels
    ("Material", "Material") have no own words, so the column's position names it: "Material 2".
    """
    split = [header[c].split() for c in columns]
    shared = 0
    while all(len(words) > shared for words in split) and len({w[shared] for w in split}) == 1:
        shared += 1
    own = " ".join(header[column].split()[shared:])
    return own or f"{header[column]} {list(columns).index(column) + 1}"


def _union_bbox(boxes: Sequence[BBox]) -> BBox:
    """Smallest box that contains all `boxes`."""
    return BBox(
        x0=min(b.x0 for b in boxes),
        y0=min(b.y0 for b in boxes),
        x1=max(b.x1 for b in boxes),
        y1=max(b.y1 for b in boxes),
    )


def _inside(block: TextBlock, box: BBox) -> bool:
    """True if the block's centre lies within `box`."""
    cx = (block.bbox.x0 + block.bbox.x1) / 2
    cy = (block.bbox.y0 + block.bbox.y1) / 2
    return box.x0 <= cx <= box.x1 and box.y0 <= cy <= box.y1


def table_row_text(header: Sequence[str], cells: Sequence[str], section: str | None) -> str:
    """Render a row as `Header: value` pairs, prefixed by the section for context.

    Args:
        header: Column labels.
        cells: The row's cell values.
        section: Heading the table sits under, if any.

    Returns:
        e.g. `ETN SERIES | Model: ETN 50; Flow (m3/h): 120; Power (kW): 7.5`.
    """
    pairs = [f"{h}: {c}" for h, c in zip(header, cells, strict=False) if c]
    # A merged cell repeated across a spanned header yields identical pairs; keep one.
    unique = [p for i, p in enumerate(pairs) if i == 0 or p != pairs[i - 1]]
    joined = "; ".join(unique)
    return f"{section} | {joined}" if section else joined


def key_value_row_text(cells: Sequence[str], context: Sequence[str | None]) -> str:
    """Render a label/value row as `label: value`, prefixed by its section and table title.

    Args:
        cells: `[label, value]`.
        context: Section heading and table title (either may be None).

    Returns:
        e.g. `Bid Details | EMD Amount: 24000`.
    """
    pair = ": ".join(c for c in cells if c)
    prefix = " | ".join(dict.fromkeys(c for c in context if c))
    return f"{prefix} | {pair}" if prefix else pair


@dataclass(frozen=True, slots=True)
class _Variant:
    """One version column of a row: its index, its name, and the columns shown with it."""

    column: int
    name: str
    shown: tuple[int, ...]


def _row_chunk(
    table: ExtractedTable,
    row_index: int,
    row: TableRow,
    section: str | None,
    model_col: int | None,
    variant: _Variant | None = None,
) -> ChunkDraft:
    """Build the chunk for one table row (or one version of it); raw cells stay in `meta`."""
    shown = variant.shown if variant else tuple(range(len(row.cells)))
    cells = [row.cells[i] for i in shown]
    if table.key_value:
        text = key_value_row_text(row.cells, (section, table.title))
    else:
        text = table_row_text([table.header[i] for i in shown], cells, section)
    model = (row.cells[model_col] or None) if model_col is not None else None
    extra: dict[str, object] = {}
    if variant is not None:
        model = f"{model} / {variant.name}" if model else variant.name
        extra = {"variant": variant.name, "variant_column": variant.column}
    return ChunkDraft(
        page_number=table.page_number,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=text,
        section=section,
        model=model,
        token_count=max(estimate_tokens(text), 1),
        meta={
            "table_index": table.index,
            "row_index": row_index,
            "header": table.header,
            "cells": row.cells,
            "raw_row": " ".join(c for c in cells if c),
            "bbox": row.bbox.model_dump(),
            "continued_from_page": table.continued_from_page,
            **({"stacked_unresolved": True} if row.stacked_unresolved else {}),
            **({"rule_grid": True} if table.from_rules else {}),
            # A value printed across these columns was re-joined (V5); check the row bbox.
            **({"spanning_columns": list(row.spanning_columns)} if row.spanning_columns else {}),
            **extra,
        },
    )


def _column_cells(table: ExtractedTable, column: int) -> list[str]:
    """Non-empty body cells of one column, top to bottom."""
    return [r.cells[column] for r in table.rows if column < len(r.cells) and r.cells[column]]


def is_option_legend(table: ExtractedTable) -> bool:
    """True for an ordering-code legend: columns of independent "code - label" options.

    Each column lists the choices for one attribute (L&T Aquaseal "Ordering Information":
    Pressure Rating "1 - PN 10", "2 - PN 16"; Body Material "I - Cast Iron", ...). A row is
    not a product, so reading it as one would combine unrelated options (B10). At least two
    columns must be fully coded, and no column may mix coded and plain cells.

    Args:
        table: An extracted table.

    Returns:
        Whether the table should be chunked by column.
    """
    if table.key_value:
        return False
    coded = 0
    for column in range(len(table.header)):
        cells = _column_cells(table, column)
        hits = sum(bool(_OPTION_CELL.match(c)) for c in cells)
        if hits and hits != len(cells):
            return False
        coded += bool(cells) and hits == len(cells)
    return coded >= _MIN_OPTION_COLUMNS


def _option_column_chunk(
    table: ExtractedTable, column: int, cells: list[str], section: str | None
) -> ChunkDraft:
    """One chunk per legend column: `section | Header: opt 1; opt 2; ...`."""
    text = table_row_text([table.header[column]], ["; ".join(cells)], section)
    return ChunkDraft(
        page_number=table.page_number,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=text,
        section=section,
        token_count=max(estimate_tokens(text), 1),
        meta={
            "table_index": table.index,
            "column_index": column,
            "header": table.header,
            "options": cells,
            "option_legend": True,
            "bbox": table.bbox.model_dump(),
            "continued_from_page": table.continued_from_page,
        },
    )


def table_chunks(
    table: ExtractedTable, section: str | None, options: ChunkerOptions
) -> list[ChunkDraft]:
    """One chunk per body row, each repeating the header, section and model.

    An ordering-code legend (see `is_option_legend`) gives one chunk per column instead.

    A table with version columns (see `find_variant_columns`) gives one chunk per row and
    version instead: the shared columns plus that one version, with the version as `model`,
    so values of different versions never share a chunk (CLAUDE.md B10).

    Args:
        table: An extracted table.
        section: Heading the table sits under.
        options: Chunker options (model- and version-column keywords).

    Returns:
        Row chunks in table order (positions are assigned per page later).
    """
    if is_option_legend(table):
        legend = [(c, _column_cells(table, c)) for c in range(len(table.header))]
        return [_option_column_chunk(table, c, cells, section) for c, cells in legend if cells]
    model_col = find_model_column(table.header, options.model_header_keywords)
    rows = [(i, row) for i, row in enumerate(table.rows) if any(row.cells)]
    columns = (
        []
        if table.key_value
        else distinct_columns(
            table.rows, find_variant_columns(table.header, options.variant_header_keywords)
        )
    )
    if len(columns) < 2:
        return [_row_chunk(table, i, row, section, model_col) for i, row in rows]
    common = tuple(i for i in range(len(table.header)) if i not in columns)
    variants = [
        _Variant(column=c, name=variant_name(table.header, columns, c), shown=(*common, c))
        for c in columns
    ]
    return [
        _row_chunk(table, i, row, section, model_col, v)
        for i, row in rows
        for v in variants
        if v.column < len(row.cells) and row.cells[v.column]
    ]


def split_long_text(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text that exceeds `max_tokens` into word windows that overlap.

    Args:
        text: Text of one block.
        max_tokens: Maximum estimated tokens per piece.
        overlap_tokens: Approximate tokens repeated at the start of the next piece.

    Returns:
        One or more pieces, each a contiguous run of the original words.
    """
    words = text.split()
    pieces: list[str] = []
    start = 0
    while start < len(words):
        end, used = start, 0
        while end < len(words) and (
            used + estimate_tokens(words[end]) <= max_tokens or end == start
        ):
            used += estimate_tokens(words[end])
            end += 1
        pieces.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = max(_overlap_start(words, start, end, overlap_tokens), start + 1)
    return pieces


def _overlap_start(words: Sequence[str], start: int, end: int, overlap_tokens: int) -> int:
    """Index from which the last ~`overlap_tokens` of `words[start:end]` begin."""
    used, i = 0, end
    while i > start and used < overlap_tokens:
        i -= 1
        used += estimate_tokens(words[i])
    return i


@dataclass(slots=True)
class _ProseBuffer:
    """Accumulates text blocks of one section on one page into prose chunks."""

    options: ChunkerOptions
    page_number: int
    section: str | None = None
    section_page: int | None = None
    blocks: list[TextBlock] = field(default_factory=list)
    chunks: list[ChunkDraft] = field(default_factory=list)
    _tail: str = ""

    def add(self, block: TextBlock) -> None:
        """Add a block, flushing first if it would push the chunk past the target size."""
        size = estimate_tokens(block.text)
        if self.blocks and self._size() + size > self.options.target_tokens:
            self.flush()
        self.blocks.append(block)

    def _size(self) -> int:
        return sum(estimate_tokens(b.text) for b in self.blocks)

    def flush(self) -> None:
        """Emit the buffered blocks as one or more chunks."""
        if not self.blocks:
            return
        text = "\n".join(b.text for b in self.blocks)
        bbox = _union_bbox([b.bbox for b in self.blocks])
        if estimate_tokens(text) <= self.options.max_tokens:
            # Keep the text verbatim (line breaks included); it is quoted as evidence later.
            pieces = [f"{self._tail}\n{text}" if self._tail else text]
        else:
            pieces = split_long_text(text, self.options.max_tokens, self.options.overlap_tokens)
        for piece in pieces:
            self._emit(piece, bbox)
        self._tail = _last_tokens(text, self.options.overlap_tokens)
        self.blocks = []

    def take_chunks(self) -> list[ChunkDraft]:
        """Flush, end the overlap (the next text is not contiguous) and return the chunks."""
        self.flush()
        self._tail = ""
        chunks, self.chunks = self.chunks, []
        return chunks

    def new_section(self, section: str) -> None:
        """Close the current section; overlap never crosses a heading."""
        self.flush()
        self.section = section
        self.section_page = self.page_number
        self._tail = ""

    def _emit(self, text: str, bbox: BBox) -> None:
        self.chunks.append(
            ChunkDraft(
                page_number=self.page_number,
                position=0,
                chunk_type=ChunkType.PROSE,
                raw_text=text,
                section=self.section,
                token_count=max(estimate_tokens(text), 1),
                meta={"bbox": bbox.model_dump(), "section_page": self.section_page},
            )
        )


def _last_tokens(text: str, overlap_tokens: int) -> str:
    """The final words of `text` totalling about `overlap_tokens` estimated tokens."""
    if overlap_tokens <= 0:
        return ""
    words = text.split()
    return " ".join(words[_overlap_start(words, 0, len(words), overlap_tokens) :])


@dataclass(frozen=True, slots=True)
class PageChunks:
    """Chunks of one page plus the section (and its heading page) open at the bottom."""

    chunks: list[ChunkDraft]
    section: str | None
    section_page: int | None = None


def _page_items(
    page: ParsedPage, tables: Sequence[ExtractedTable]
) -> list[TextBlock | ExtractedTable]:
    """Blocks outside tables plus the tables, in top-to-bottom, left-to-right order."""
    blocks: list[TextBlock | ExtractedTable] = [
        b for b in page.blocks if not any(_inside(b, t.bbox) for t in tables)
    ]
    items = [*blocks, *tables]
    return sorted(items, key=lambda item: (item.bbox.y0, item.bbox.x0))


def chunk_page(
    page: ParsedPage,
    tables: Sequence[ExtractedTable],
    options: ChunkerOptions,
    section: str | None = None,
    section_page: int | None = None,
) -> PageChunks:
    """Chunk one page in reading order.

    A heading in the page's bottom margin band ("A Kirloskar Group Company") heads what
    follows it on its own page only: the next page continues the section that was open before
    it, so a footer never becomes the section of a whole page (failure case V8).

    Args:
        page: The parsed page.
        tables: Tables found on this page.
        options: Chunker thresholds.
        section: Heading carried over from the previous page.
        section_page: Page that heading was found on.

    Returns:
        The page's chunks (positions 0..n-1 in reading order) and the section still open.
    """
    prose = _ProseBuffer(
        options=options,
        page_number=page.page_number,
        section=section,
        section_page=section_page,
    )
    ordered: list[ChunkDraft] = []
    carried = (section, section_page)  # what the next page continues
    for item in _page_items(page, tables):
        if isinstance(item, ExtractedTable):
            ordered.extend(prose.take_chunks())
            ordered.extend(
                c.model_copy(update={"meta": {**c.meta, "section_page": prose.section_page}})
                for c in table_chunks(item, _table_section(item, prose.section), options)
            )
        elif is_heading(
            item.text,
            options.heading_max_words,
            options.heading_min_latin_ratio,
            options.heading_max_digit_share,
        ):
            prose.new_section(item.text.strip())
            if not _in_footer(item, page.height, options.footer_band):
                carried = (prose.section, prose.section_page)
        else:
            prose.add(item)
    ordered.extend(prose.take_chunks())
    kept = [c for c in ordered if has_content(c.raw_text)]
    numbered = [c.model_copy(update={"position": i}) for i, c in enumerate(kept)]
    return PageChunks(chunks=numbered, section=carried[0], section_page=carried[1])


def _table_section(table: ExtractedTable, section: str | None) -> str | None:
    """A catalogue table's caption ("PERFORMANCE CHART FOR ...") is the section of its rows.

    Key-value tables keep the page section: their title is already in every row's text.
    """
    return table.title if table.title and not table.key_value else section


def _in_footer(block: TextBlock, page_height: float, band: float) -> bool:
    """True if the block lies in the bottom `band` of the page (never when band is 0)."""
    return band > 0 and in_bottom_band(block.bbox, page_height, band)


def chunk_document(
    pages: Sequence[ParsedPage],
    tables_by_page: Mapping[int, Sequence[ExtractedTable]],
    options: ChunkerOptions,
) -> list[ChunkDraft]:
    """Chunk every page, carrying the open section heading from one page to the next.

    Args:
        pages: Parsed pages in order.
        tables_by_page: Tables keyed by 1-based page number.
        options: Chunker thresholds.

    Returns:
        All chunks, ordered by page then position.
    """
    chunks: list[ChunkDraft] = []
    section: str | None = None
    section_page: int | None = None
    for page in pages:
        tables = tables_by_page.get(page.page_number, ())
        result = chunk_page(page, tables, options, section, section_page)
        chunks.extend(result.chunks)
        section, section_page = result.section, result.section_page
    return chunks
