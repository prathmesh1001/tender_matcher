"""Which product a catalogue chunk belongs to, by the section headings naming a product.

One PDF can hold several models ("Aquaseal 10", "Aquaseal 16"), so a value may hold for a sibling
and not for the product being matched (CLAUDE.md B10). A heading naming exactly one product type
holds until the next heading. A heading naming several ("Butterfly & Check Valves") is about the
whole document, and an overview page naming many models holds no one model's values: their chunks
are unclear. This is not a model lock: it only says where evidence comes from.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import CatalogueChunk
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.enums import ChunkType

# A variant of the same heading: "Aquaseal 16 ... - PN 16 (Flanged)".
_VARIANT: Final = re.compile(r"\s*\([^)]*\)\s*$")
# A page title's lead ends at a dash, colon or bracket: "Gate Valves - ASME Classes 150".
_TITLE_LEAD_END: Final = re.compile(r"\s+[-\u2013\u2014:|]\s+|\(")
_LETTER: Final = re.compile(r"[^\W\d_]")


@dataclass(frozen=True, slots=True)
class ProductGroups:
    """A document's chunks by product heading, with the chunks no heading clearly owns.

    `unowned` holds the pages no product heading owns or starts on (front matter, ordering pages
    of the whole document), with the product types their text names.
    """

    by_heading: dict[str, list[CatalogueChunk]]
    unclear: list[CatalogueChunk]
    types: dict[str, str]  # heading -> the product type it names
    unowned: dict[int, frozenset[str]] = field(default_factory=dict)

    def headings_of(self, product_type: str) -> list[str]:
        """The headings naming this product type, in reading order."""
        return [h for h in self.by_heading if self.types[h] == product_type]

    def shared_pages(self, product_type: str) -> frozenset[int]:
        """Unowned pages about the whole document: naming no product type, several, or this one.

        A page naming exactly one other product type is about that product, not shared.
        """
        return frozenset(
            page for page, named in self.unowned.items() if len(named) != 1 or product_type in named
        )

    def pages_of(self, headings: Sequence[str]) -> frozenset[int]:
        """The pages holding chunks of these headings."""
        return frozenset(c.chunk.page_number for h in headings for c in self.by_heading[h])


def _section_types(
    section: str | None,
    matcher: ProductMatcher,
    vocabulary: SearchVocabulary,
    known: frozenset[str],
) -> set[str]:
    """The product types a section heading names."""
    return matcher.types_in(normalize_for_search(section, vocabulary), known) if section else set()


@dataclass(frozen=True, slots=True)
class _Reader:
    """What reading headings needs: the ontology's product words and the title word limit."""

    matcher: ProductMatcher
    vocabulary: SearchVocabulary
    known: frozenset[str]
    title_max_words: int

    def types(self, text: str) -> set[str]:
        """The product types the text names."""
        return self.matcher.types_in(normalize_for_search(text, self.vocabulary), self.known)


# A chunk's effect on the heading: a new heading (text, type), WIDE (about several products), or
# None (the current heading goes on).
_WIDE: Final = ("", "")
_Start = tuple[str, str] | None


def _captions(ordered: Sequence[CatalogueChunk]) -> frozenset[str]:
    """Sections whose first chunk holds no word: a figure caption above the page number ("08")."""
    first: dict[str, CatalogueChunk] = {}
    for c in ordered:
        if c.chunk.section:
            first.setdefault(c.chunk.section, c)
    return frozenset(s for s, c in first.items() if not _LETTER.search(c.chunk.raw_text))


def _page_title(text: str, reader: _Reader) -> _Start:
    """A page's first line as a product title: "Gate Valves - ASME Classes 150, 300 & 600".

    Its lead (up to a dash, colon or bracket, else the whole line) is short and names exactly one
    product type, and the whole line names no other. A short lead in a line naming several types
    ("Gate & Globe Valves - ASME B16.34") is a title of the whole range: WIDE.
    """
    line = text.strip().split("\n", 1)[0].strip()
    lead = _TITLE_LEAD_END.split(line, maxsplit=1)[0].strip()
    if not lead or len(lead.split()) > reader.title_max_words:
        return None
    in_lead, in_line = reader.types(lead), reader.types(line)
    if len(in_line) > 1:
        return _WIDE
    if len(in_lead) == 1 and in_line == in_lead:
        return _VARIANT.sub("", line), next(iter(in_lead))
    return None


def _starts(ordered: Sequence[CatalogueChunk], reader: _Reader) -> list[_Start]:
    """Each chunk's effect on the heading: its section, or the title of a page opening with text.

    Page titles are read only in a document declaring several product types, where they tell
    the products apart ("Gate Valves - ...", "Globe Valves - ..."). In a one-product
    catalogue they would split its range pages from its variants.
    """
    titled = bool(ordered) and len(ordered[0].document.product_types) > 1
    captions = _captions(ordered)
    starts: list[_Start] = []
    seen_pages: set[int] = set()
    for c in ordered:
        section = c.chunk.section if c.chunk.section not in captions else None
        types = reader.types(section) if section else set()
        start: _Start = None
        if len(types) == 1:
            start = _VARIANT.sub("", section or ""), next(iter(types))
        elif types:
            start = _WIDE
        if c.chunk.page_number not in seen_pages:
            seen_pages.add(c.chunk.page_number)
            if titled and c.chunk.chunk_type is ChunkType.PROSE:
                start = _page_title(c.chunk.raw_text, reader) or start
        starts.append(start)
    return starts


def _shared_pages(
    ordered: Sequence[CatalogueChunk], owned: set[int], reader: _Reader
) -> dict[int, frozenset[str]]:
    """Pages no heading owns or starts on, with the product types their text names."""
    named: dict[int, set[str]] = {}
    for c in ordered:
        if c.chunk.page_number not in owned:
            named.setdefault(c.chunk.page_number, set()).update(reader.types(c.chunk.raw_text))
    return {page: frozenset(types) for page, types in named.items()}


def group_by_product(
    chunks: Sequence[CatalogueChunk],
    ontology: Ontology,
    vocabulary: SearchVocabulary,
    overview_min_headings: int,
    page_title_max_words: int,
) -> ProductGroups:
    """Group one document's chunks by the product heading above them.

    A heading is a section naming one product type, or a page's title line naming one. A section
    holding no word (a figure caption above the page number) is no heading.

    Args:
        chunks: One document's chunks.
        ontology: Its product types and their words.
        vocabulary: Search vocabulary of the ontology.
        overview_min_headings: A page naming this many products is an overview.
        page_title_max_words: Most words in a page title's lead.

    Returns:
        The groups. Chunks before the first heading, under a document-wide heading or on an
        overview page are unclear.
    """
    ordered = sorted(chunks, key=lambda c: (c.chunk.page_number, c.chunk.position))
    reader = _Reader(
        ProductMatcher.from_ontologies([ontology], vocabulary),
        vocabulary,
        frozenset(p.name for p in ontology.product_types),
        page_title_max_words,
    )
    starts = _starts(ordered, reader)
    on_page: dict[int, set[str]] = {}
    for item, start in zip(ordered, starts, strict=True):
        if start is not None and start is not _WIDE:
            on_page.setdefault(item.chunk.page_number, set()).add(start[0])
    by_heading: dict[str, list[CatalogueChunk]] = {}
    heading_types: dict[str, str] = {}
    unclear: list[CatalogueChunk] = []
    current: str | None = None
    for item, start in zip(ordered, starts, strict=True):
        if len(on_page.get(item.chunk.page_number, ())) >= overview_min_headings or start is _WIDE:
            current = None
        elif start is not None:
            current, heading_types[start[0]] = start
        if current is None:
            unclear.append(item)
        else:
            by_heading.setdefault(current, []).append(item)
    owned = {c.chunk.page_number for cs in by_heading.values() for c in cs} | set(on_page)
    return ProductGroups(by_heading, unclear, heading_types, _shared_pages(ordered, owned, reader))


def other_product_passages(
    groups: ProductGroups, product_type: str, chosen: str | None
) -> frozenset[tuple[int, str]]:
    """The chunks under a heading of another product than the item's, as (page, raw_text).

    A heading of another product type is never the item's. A sibling heading of the same type
    is another product only once one of them is chosen; before that nothing tells them apart.

    Args:
        groups: The document's chunks by product heading.
        product_type: The item's product type.
        chosen: The heading chosen for the item, or None.

    Returns:
        The page and verbatim text of each such chunk.
    """
    return frozenset(
        (c.chunk.page_number, c.chunk.raw_text)
        for heading, chunks in groups.by_heading.items()
        if groups.types[heading] != product_type or chosen not in (None, heading)
        for c in chunks
    )
