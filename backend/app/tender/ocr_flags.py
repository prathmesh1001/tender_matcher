"""Flag requirements read from low-confidence OCR, so their results go to review. Pure functions.

A scanned page can be read well on the whole and still garble one row ("35HP/26.1K W"). Two
signals are used: the page's mean confidence (`low_confidence_pages` in `quality.py`) and the
mean word confidence of the scanned table row the requirement came from. The flag changes no
status: a flagged requirement's results get `needs_review` (comparator) and lower confidence.
"""

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Final

from app.schemas.chunking import ChunkDraft
from app.schemas.requirements import Requirement
from app.tender.items import TenderRequirements
from app.tender.pipeline import ItemRequirements
from app.tender.segments import row_line

LOW_OCR_FLAG: Final = "read from a low-confidence scan: check the document"

# (file name, page number, row text) of one scanned table row.
LowRow = tuple[str, int, str]


def low_confidence_rows(
    document: str, chunks: Sequence[ChunkDraft], min_confidence: float
) -> set[LowRow]:
    """Scanned table rows of one file whose words were read below the confidence.

    Args:
        document: File name, as requirements cite it.
        chunks: The file's chunks; only rows rebuilt from OCR carry `ocr_confidence`.
        min_confidence: Lowest mean word confidence to trust.

    Returns:
        (file name, page, row text as "label : value") of each such row.
    """
    out: set[LowRow] = set()
    for chunk in chunks:
        confidence = chunk.meta.get("ocr_confidence")
        if isinstance(confidence, float) and confidence < min_confidence:
            out.add((document, chunk.page_number, _key(row_line(chunk))))
    return out


def _key(text: str) -> str:
    """Text compared case- and space-insensitively."""
    return " ".join(text.lower().split())


def is_low_ocr(requirement: Requirement, pages: set[tuple[str, int]], rows: set[LowRow]) -> bool:
    """True if the requirement sits on a low-confidence page or comes from a low row.

    Args:
        requirement: Any requirement.
        pages: (file, page) of low-confidence OCR pages.
        rows: Low-confidence scanned rows.

    Returns:
        Whether it should be checked against the scan.
    """
    place = (requirement.document, requirement.page_number)
    if place in pages:
        return True
    text = _key(requirement.raw_text)
    return bool(text) and any(
        (doc, page) == place and (text in row or row in text) for doc, page, row in rows
    )


def _flagged(
    requirements: Iterable[Requirement], pages: set[tuple[str, int]], rows: set[LowRow]
) -> list[Requirement]:
    """The requirements, those read from a low-confidence scan flagged once."""
    return [
        (
            r.model_copy(update={"flags": [*r.flags, LOW_OCR_FLAG]})
            if LOW_OCR_FLAG not in r.flags and is_low_ocr(r, pages, rows)
            else r
        )
        for r in requirements
    ]


def flag_low_ocr(
    found: TenderRequirements, pages: set[tuple[str, int]], rows: set[LowRow]
) -> TenderRequirements:
    """Flag every requirement read from a low-confidence scan.

    Args:
        found: The tender's requirements.
        pages: (file, page) of low-confidence OCR pages.
        rows: Low-confidence scanned table rows.

    Returns:
        A copy with the flag added; unchanged when nothing is low.
    """
    if not pages and not rows:
        return found
    items = [
        ItemRequirements(e.item, e.category, _flagged(e.requirements, pages, rows), e.product_type)
        for e in found.items
    ]
    return replace(found, items=items, general=_flagged(found.general, pages, rows))
