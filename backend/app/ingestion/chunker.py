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
from app.schemas.chunking import ChunkDraft, ExtractedTable, TableRow
from app.schemas.enums import ChunkType
from app.schemas.parsing import BBox, ParsedPage, TextBlock

_TOKEN: Final = re.compile(r"\w+|[^\w\s]")
_NUMBERED_HEADING: Final = re.compile(r"^\d+(\.\d+)*\.?\s+\S")
_KEY_VALUE: Final = re.compile(r":\s*\S")


@dataclass(frozen=True, slots=True)
class ChunkerOptions:
    """Chunking thresholds (built from settings so the chunker never reads config)."""

    target_tokens: int
    max_tokens: int
    overlap_tokens: int
    heading_max_words: int
    model_header_keywords: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings: Settings) -> ChunkerOptions:
        """Build options from application settings."""
        return cls(
            target_tokens=settings.CHUNK_TARGET_TOKENS,
            max_tokens=settings.CHUNK_MAX_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
            heading_max_words=settings.CHUNK_HEADING_MAX_WORDS,
            model_header_keywords=tuple(k.lower() for k in settings.CHUNK_MODEL_HEADER_KEYWORDS),
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


def is_heading(text: str, max_words: int) -> bool:
    """Guess whether a text block is a section heading.

    A heading is one short line that is either numbered ("3.2 Materials"), ALL CAPS, or Title
    Case, and is not a sentence (no final period) or a "key: value" spec line.

    Args:
        text: A text block.
        max_words: Longer blocks are never headings.

    Returns:
        True if the block looks like a heading.
    """
    stripped = text.strip()
    words = stripped.split()
    if not words or "\n" in stripped or len(words) > max_words:
        return False
    if stripped.endswith(".") or _KEY_VALUE.search(stripped):
        return False
    letters = [ch for ch in stripped if ch.isalpha()]
    if not letters:
        return False
    if _NUMBERED_HEADING.match(stripped) or stripped.isupper():
        return True
    alpha_words = [w for w in words if w[0].isalpha()]
    return bool(alpha_words) and all(w[0].isupper() for w in alpha_words)


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
    pairs = "; ".join(f"{h}: {c}" for h, c in zip(header, cells, strict=False) if c)
    return f"{section} | {pairs}" if section else pairs


def _row_chunk(
    table: ExtractedTable, row_index: int, row: TableRow, section: str | None, model_col: int | None
) -> ChunkDraft:
    """Build the chunk for one table row; raw cells are kept in `meta` for quote checks."""
    text = table_row_text(table.header, row.cells, section)
    model = (row.cells[model_col] or None) if model_col is not None else None
    return ChunkDraft(
        page_number=table.page_number,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        text=text,
        section=section,
        model=model,
        token_count=max(estimate_tokens(text), 1),
        meta={
            "table_index": table.index,
            "row_index": row_index,
            "header": table.header,
            "cells": row.cells,
            "raw_row": " ".join(c for c in row.cells if c),
            "bbox": row.bbox.model_dump(),
        },
    )


def table_chunks(
    table: ExtractedTable, section: str | None, options: ChunkerOptions
) -> list[ChunkDraft]:
    """One chunk per body row, each repeating the header, section and model.

    Args:
        table: An extracted table.
        section: Heading the table sits under.
        options: Chunker options (for the model-column keywords).

    Returns:
        Row chunks in table order (positions are assigned per page later).
    """
    model_col = find_model_column(table.header, options.model_header_keywords)
    return [
        _row_chunk(table, i, row, section, model_col)
        for i, row in enumerate(table.rows)
        if any(row.cells)
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
        self._tail = ""

    def _emit(self, text: str, bbox: BBox) -> None:
        self.chunks.append(
            ChunkDraft(
                page_number=self.page_number,
                position=0,
                chunk_type=ChunkType.PROSE,
                text=text,
                section=self.section,
                token_count=max(estimate_tokens(text), 1),
                meta={"bbox": bbox.model_dump()},
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
    """Chunks of one page plus the section that is open at the bottom of the page."""

    chunks: list[ChunkDraft]
    section: str | None


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
) -> PageChunks:
    """Chunk one page in reading order.

    Args:
        page: The parsed page.
        tables: Tables found on this page.
        options: Chunker thresholds.
        section: Heading carried over from the previous page.

    Returns:
        The page's chunks (positions 0..n-1 in reading order) and the section still open.
    """
    prose = _ProseBuffer(options=options, page_number=page.page_number, section=section)
    ordered: list[ChunkDraft] = []
    for item in _page_items(page, tables):
        if isinstance(item, ExtractedTable):
            ordered.extend(prose.take_chunks())
            ordered.extend(table_chunks(item, prose.section, options))
        elif is_heading(item.text, options.heading_max_words):
            prose.new_section(item.text.strip())
        else:
            prose.add(item)
    ordered.extend(prose.take_chunks())
    numbered = [c.model_copy(update={"position": i}) for i, c in enumerate(ordered)]
    return PageChunks(chunks=numbered, section=prose.section)


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
    for page in pages:
        result = chunk_page(page, tables_by_page.get(page.page_number, ()), options, section)
        chunks.extend(result.chunks)
        section = result.section
    return chunks
