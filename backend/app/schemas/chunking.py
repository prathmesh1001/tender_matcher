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
    model: str | None = None
    token_count: int = Field(ge=1)
    meta: dict[str, Any] = Field(default_factory=dict)
