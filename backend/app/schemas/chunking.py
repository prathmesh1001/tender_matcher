"""Typed output of table extraction and chunking."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import ChunkType
from app.schemas.parsing import BBox


class TableRow(BaseModel):
    """One body row of a table. Merged cells are already filled down from the row above."""

    model_config = ConfigDict(frozen=True)

    cells: list[str]
    bbox: BBox
    stacked_unresolved: bool = Field(
        default=False,
        description="Holds several stacked items that could not be split safely; review it.",
    )
    cell_lines: list[list[str]] = Field(
        default_factory=list,
        description=(
            "Each cell's text lines in reading order, kept only when a cell has several "
            '(one "KEY : value" per line in tender specs). Empty otherwise.'
        ),
    )
    cell_line_ys: list[list[float | None]] = Field(
        default_factory=list,
        description=(
            "Top y of each line in `cell_lines` (None at a paragraph break), so a line can be "
            "matched to the key line printed at the same height. Empty when unknown."
        ),
    )
    spanning_columns: tuple[int, ...] = Field(
        default=(),
        description="Columns holding one value that was printed across them and re-joined.",
    )


class ExtractedTable(BaseModel):
    """A table found on a page, with its header separated from its body rows."""

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    index: int = Field(ge=0, description="Order of the table on its page.")
    bbox: BBox
    header: list[str]
    rows: list[TableRow]
    continued_from_page: int | None = Field(
        default=None, description="Set when this table continues one from an earlier page."
    )
    key_value: bool = Field(
        default=False, description="Rows are `label | value` pairs; there is no column header."
    )
    title: str | None = Field(default=None, description="Title row spanning the whole table.")
    from_rules: bool = Field(
        default=False, description="Rebuilt from drawn rules (layout.py), not found by PyMuPDF."
    )


class ChunkDraft(BaseModel):
    """A chunk ready to be stored as a `Chunk` row (document id is attached by the pipeline)."""

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    position: int = Field(ge=0, description="Order of the chunk within its page.")
    chunk_type: ChunkType
    raw_text: str = Field(min_length=1, description="Verbatim text; quotes come from here.")
    search_text: str = Field(default="", description="normalize_for_search(raw_text).")
    section: str | None = None
    product_family: str | None = None
    product_type: str | None = Field(default=None, description="None = general.")
    product_sub_type: str | None = None
    model: str | None = None
    token_count: int = Field(ge=1)
    meta: dict[str, Any] = Field(default_factory=dict)
