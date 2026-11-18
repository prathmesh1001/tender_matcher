"""A GeM Marketplace listing as pages and chunks, through the same steps as a catalogue.

The listing's specification is a label/value table, so it goes through the table chunker as
one: one chunk per row, the brand and model repeated in every chunk as the table title and the
spec group as the section (CLAUDE.md section 6, table rows). Everything is on page 1. The page
text holds every row as `label: value`, so quotes verify on it like on a catalogue page.
"""

from collections.abc import Sequence
from typing import Final

from app.discovery.gem_parser import GemListing, SpecRow
from app.ingestion.chunker import table_chunks
from app.ingestion.processing import (
    ProcessedDocument,
    ProcessedPage,
    ProcessingOptions,
    add_search_text,
    set_product_family,
    tag_products,
)
from app.ingestion.product_tagger import Declared
from app.schemas.chunking import ChunkDraft, ExtractedTable, TableRow
from app.schemas.parsing import BBox

LISTING_PAGE: Final = 1
_NO_BOX: Final = BBox(x0=0, y0=0, x1=0, y1=0)


def listing_name(listing: GemListing) -> str:
    """Brand and model as one name ("Unbranded / Festo GRLA-3"); the title if neither is given."""
    parts = [p for p in (listing.brand, listing.model) if p]
    return " / ".join(parts) if parts else listing.title


def listing_page_text(listing: GemListing) -> str:
    """The text stored as the listing's one page: title, brand, model and every row."""
    head = [listing.title, f"Brand: {listing.brand or ''}", f"Model: {listing.model or ''}"]
    rows = [f"{r.label}: {r.value}" for r in listing.specs]
    return "\n".join([*head, *rows])


def _groups(specs: Sequence[SpecRow]) -> list[tuple[str | None, list[SpecRow]]]:
    """Rows in printed order, grouped by consecutive spec group."""
    out: list[tuple[str | None, list[SpecRow]]] = []
    for row in specs:
        if out and out[-1][0] == row.group:
            out[-1][1].append(row)
        else:
            out.append((row.group, [row]))
    return out


def listing_chunks(listing: GemListing, options: ProcessingOptions) -> list[ChunkDraft]:
    """One table-row chunk per specification row, brand/model repeated, positions in order.

    Args:
        listing: A parsed listing.
        options: Processing options (chunker settings).

    Returns:
        The chunks (search text not filled yet).
    """
    name = listing_name(listing)
    chunks: list[ChunkDraft] = []
    for index, (group, rows) in enumerate(_groups(listing.specs)):
        table = ExtractedTable(
            page_number=LISTING_PAGE,
            index=index,
            bbox=_NO_BOX,
            header=[],
            rows=[TableRow(cells=[r.label, r.value], bbox=_NO_BOX) for r in rows],
            key_value=True,
            title=name,
        )
        chunks.extend(table_chunks(table, group, options.chunker))
    return [
        c.model_copy(update={"position": i, "model": listing.model or c.model})
        for i, c in enumerate(chunks)
    ]


def process_listing(
    listing: GemListing, product_type: str, options: ProcessingOptions
) -> ProcessedDocument:
    """Pages and tagged chunks of one listing, like `catalogue_processor` does for a file.

    Args:
        listing: A parsed listing that passed the checks.
        product_type: The product type it was found for (the only type it declares).
        options: Processing options (chunker, vocabulary, product matcher).

    Returns:
        One page and its chunks, tagged by product and in the listing's family (its title).
    """
    page = ProcessedPage(page_number=LISTING_PAGE, text=listing_page_text(listing), ocr=False)
    chunks = add_search_text(listing_chunks(listing, options), options.vocabulary)
    processed = ProcessedDocument(pages=[page], chunks=chunks)
    processed = tag_products(processed, options, Declared((product_type,), ()))
    return set_product_family(processed, listing.title)
