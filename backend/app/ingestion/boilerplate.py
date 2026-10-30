"""Find and drop running headers and footers (boilerplate) before chunking (failure case V8).

A company footer ("KIRLOSKAR PUMPS"), a bulletin header or a distributor banner printed on
every page is not evidence. Used as a heading it would become the section of every chunk
under it, and section routing and product tagging would follow the footer instead of the
content.

A line is boilerplate when its block lies wholly inside the top or bottom margin band of the
page, and the same line (digits folded, so "Page 1 of 2" matches "Page 2 of 2") sits in a
margin band on enough pages, and never in the body of any page. These conditions are needed
together: on a two-page catalogue a series name ("KDI") or a table value is on every page
too, but not in the margins; a chart axis label ("TOTAL HEAD IN METERS") can touch the top
band, but it is repeated over the charts lower down as well.

Only chunk input is cleaned: `ParsedPage.text` keeps every line, because quotes are verified
against the page text.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings
from app.schemas.parsing import BBox, ParsedPage, TextBlock

_DIGITS: Final = re.compile(r"\d+")
_SPACES: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class BoilerplateOptions:
    """Boilerplate thresholds (built from settings so this module never reads config)."""

    margin_band: float
    min_page_ratio: float
    min_pages: int
    min_letters: int

    @classmethod
    def from_settings(cls, settings: Settings) -> BoilerplateOptions:
        """Build options from application settings."""
        return cls(
            margin_band=settings.PAGE_MARGIN_BAND_FRACTION,
            min_page_ratio=settings.BOILERPLATE_MIN_PAGE_RATIO,
            min_pages=settings.BOILERPLATE_MIN_PAGES,
            min_letters=settings.BOILERPLATE_MIN_LETTERS,
        )


def line_key(text: str) -> str:
    """Comparison key of one line: NFKC, case-folded, spaces collapsed, digit runs as "#".

    Args:
        text: One line of a text block.

    Returns:
        e.g. "page # of #" for "Page 2 of 2".
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _SPACES.sub(" ", _DIGITS.sub("#", folded)).strip()


def in_top_band(bbox: BBox, page_height: float, band: float) -> bool:
    """True if the box lies wholly inside the top `band` share of the page."""
    return bbox.y1 <= page_height * band


def in_bottom_band(bbox: BBox, page_height: float, band: float) -> bool:
    """True if the box lies wholly inside the bottom `band` share of the page."""
    return bbox.y0 >= page_height * (1 - band)


def in_margin_band(bbox: BBox, page_height: float, band: float) -> bool:
    """True if the box lies wholly inside the top or bottom margin band."""
    return in_top_band(bbox, page_height, band) or in_bottom_band(bbox, page_height, band)


def _letter_count(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def line_keys(page: ParsedPage, options: BoilerplateOptions, *, margin: bool) -> set[str]:
    """Keys of the page's lines, in margin blocks or in body blocks, with enough letters.

    Args:
        page: A parsed page.
        options: Boilerplate thresholds.
        margin: True for lines of margin blocks, False for lines of body blocks.

    Returns:
        Distinct line keys found in that part of the page.
    """
    return {
        line_key(line)
        for block in page.blocks
        if in_margin_band(block.bbox, page.height, options.margin_band) == margin
        for line in block.text.splitlines()
        if _letter_count(line) >= options.min_letters
    }


def find_boilerplate(pages: Sequence[ParsedPage], options: BoilerplateOptions) -> frozenset[str]:
    """Line keys that repeat in the margins on enough pages of one document.

    Args:
        pages: All parsed pages of one document.
        options: Boilerplate thresholds.

    Returns:
        Keys of boilerplate lines; empty for documents shorter than `min_pages`.
    """
    needed = max(options.min_pages, math.ceil(options.min_page_ratio * len(pages)))
    if len(pages) < needed:
        return frozenset()
    counts: Counter[str] = Counter()
    body: set[str] = set()
    for page in pages:
        counts.update(line_keys(page, options, margin=True))
        body |= line_keys(page, options, margin=False)
    return frozenset(key for key, n in counts.items() if n >= needed and key not in body)


def strip_block(block: TextBlock, boilerplate: frozenset[str]) -> TextBlock | None:
    """Remove boilerplate lines from one block.

    The box is kept as it was: it still bounds the remaining lines, only less tightly.

    Args:
        block: A margin text block.
        boilerplate: Keys from `find_boilerplate`.

    Returns:
        The block without those lines, or None if nothing with letters or digits is left.
    """
    lines = block.text.splitlines()
    kept = [line for line in lines if line_key(line) not in boilerplate]
    if len(kept) == len(lines):
        return block
    if not any(ch.isalnum() for line in kept for ch in line):
        return None
    return block.model_copy(update={"text": "\n".join(kept).strip()})


def strip_page(page: ParsedPage, boilerplate: frozenset[str], band: float) -> ParsedPage:
    """Drop boilerplate lines from the page's margin blocks; body blocks are never touched.

    Args:
        page: A parsed page.
        boilerplate: Keys from `find_boilerplate`.
        band: Margin band share of the page height.

    Returns:
        The page with cleaned blocks and its original `text`.
    """
    blocks: list[TextBlock] = []
    for block in page.blocks:
        cleaned = (
            strip_block(block, boilerplate)
            if in_margin_band(block.bbox, page.height, band)
            else block
        )
        if cleaned is not None:
            blocks.append(cleaned)
    return page.model_copy(update={"blocks": blocks})


def remove_boilerplate(
    pages: Sequence[ParsedPage], options: BoilerplateOptions
) -> tuple[list[ParsedPage], frozenset[str]]:
    """Find the document's boilerplate lines and drop them from every page's blocks.

    Args:
        pages: All parsed pages of one document.
        options: Boilerplate thresholds.

    Returns:
        The cleaned pages and the boilerplate keys that were removed.
    """
    boilerplate = find_boilerplate(pages, options)
    if not boilerplate:
        return list(pages), boilerplate
    return [strip_page(p, boilerplate, options.margin_band) for p in pages], boilerplate
