"""Line items from a tender schedule table (BOQ / buyer specification), by header labels."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings
from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.schemas.requirements import LineItem

# An item number: "10", "1.2", "A3", "4." or "5)" (same shape the table extractor splits on).
_ITEM_NO: Final = re.compile(r"^[A-Za-z]{0,2}\d{1,4}(?:\.\d{1,3})*[A-Za-z]?[.)]?$")
_WORD: Final = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ItemColumnOptions:
    """Header labels that name a schedule's columns (folded words; see config)."""

    item_no: tuple[str, ...]
    title: tuple[str, ...]
    description: tuple[str, ...]
    quantity_prefixes: tuple[str, ...]
    unit: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings: Settings) -> ItemColumnOptions:
        """Build options from application settings."""
        return cls(
            item_no=tuple(settings.TENDER_ITEM_NO_HEADERS),
            title=tuple(settings.TENDER_ITEM_TITLE_HEADERS),
            description=tuple(settings.TENDER_ITEM_DESCRIPTION_HEADERS),
            quantity_prefixes=tuple(settings.TENDER_ITEM_QUANTITY_HEADERS),
            unit=tuple(settings.TENDER_ITEM_UNIT_HEADERS),
        )


@dataclass(frozen=True, slots=True)
class ItemColumns:
    """Column indexes of one schedule table."""

    item_no: int
    description: tuple[int, ...]
    title: int | None = None
    quantity: int | None = None
    unit: int | None = None


def header_key(label: str) -> str:
    """Folded words of a header label: "S. no." -> "s no", "Item No" -> "item no"."""
    return " ".join(_WORD.findall(fold_text(label)))


def _contains(key: str, phrase: str) -> bool:
    return f" {phrase} " in f" {key} "


def find_columns(header: Sequence[str], options: ItemColumnOptions) -> ItemColumns | None:
    """Locate the item-number, title, description, quantity and unit columns.

    Args:
        header: Table header labels.
        options: Header labels per column role.

    Returns:
        The columns, or None if there is no item-number or no description column.
    """
    keys = [header_key(h) for h in header]
    item_no = next((i for i, k in enumerate(keys) if k in options.item_no), None)
    title = next(
        (i for i, k in enumerate(keys) if any(_contains(k, p) for p in options.title)), None
    )
    description = tuple(
        i
        for i, k in enumerate(keys)
        if i not in {item_no, title} and any(_contains(k, p) for p in options.description)
    )
    if item_no is None or not description:
        return None
    quantity = next(
        (
            i
            for i, k in enumerate(keys)
            if any(w.startswith(options.quantity_prefixes) for w in k.split())
        ),
        None,
    )
    unit = next((i for i, k in enumerate(keys) if k in options.unit), None)
    return ItemColumns(item_no, description, title, quantity, unit)


def _cell(cells: Sequence[str], i: int | None) -> str:
    return cells[i].strip() if i is not None and i < len(cells) else ""


def _description_lines(chunk: ChunkDraft, columns: ItemColumns) -> list[str]:
    """Printed lines of the description columns, "" between columns."""
    cells: list[str] = chunk.meta.get("cells", [])
    cell_lines: list[list[str]] = chunk.meta.get("cell_lines", [])
    lines: list[str] = []
    for i in columns.description:
        column = cell_lines[i] if i < len(cell_lines) and cell_lines[i] else [_cell(cells, i)]
        if lines and any(column):
            lines.append("")
        lines.extend(line.replace('""', '"').strip('"') for line in column)
    return lines


def _title_from(lines: Sequence[str], fallback: str) -> str:
    """A title when the table has no title column: the first line up to its first comma."""
    first = next((ln for ln in lines if ln.strip()), "")
    return first.split(",", 1)[0].strip() or fallback


def is_schedule_row(chunk: ChunkDraft, options: ItemColumnOptions) -> bool:
    """True if the chunk is a schedule row whose item-number cell holds an item number."""
    if chunk.chunk_type is not ChunkType.TABLE_ROW or "header" not in chunk.meta:
        return False
    columns = find_columns(chunk.meta["header"], options)
    return columns is not None and bool(
        _ITEM_NO.match(_cell(chunk.meta.get("cells", []), columns.item_no))
    )


def line_items(
    chunks: Sequence[ChunkDraft], document: str, options: ItemColumnOptions
) -> list[LineItem]:
    """Every schedule row of one tender file whose item-number cell holds an item number.

    Args:
        chunks: The file's chunks (table rows carry `header`, `cells`, `cell_lines` meta).
        document: File name, for citations.
        options: Header labels per column role.

    Returns:
        The line items in document order.
    """
    items: list[LineItem] = []
    for chunk in chunks:
        if not is_schedule_row(chunk, options):
            continue
        header: list[str] = chunk.meta["header"]
        columns = find_columns(header, options)
        if columns is None:
            continue
        cells: list[str] = chunk.meta.get("cells", [])
        item_no = _cell(cells, columns.item_no)
        lines = _description_lines(chunk, columns)
        title = _cell(cells, columns.title) or _title_from(lines, item_no)
        items.append(
            LineItem(
                item_no=item_no.rstrip(".)"),
                title=title,
                description_lines=lines,
                description_header=" ".join(header[i] for i in columns.description),
                quantity=_cell(cells, columns.quantity) or None,
                unit=_cell(cells, columns.unit) or None,
                document=document,
                page_number=chunk.page_number,
            )
        )
    return items
