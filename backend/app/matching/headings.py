"""Which product a catalogue chunk belongs to, by the section headings naming a product.

One PDF can hold several models ("Aquaseal 10", "Aquaseal 16"), so a value may hold for a sibling
and not for the product being matched (CLAUDE.md B10). A heading naming exactly one product type
holds until the next heading. A heading naming several ("Butterfly & Check Valves") is about the
whole document, and an overview page naming many models holds no one model's values: their chunks
are unclear. This is not a model lock: it only says where evidence comes from.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import CatalogueChunk
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search

# A variant of the same heading: "Aquaseal 16 ... - PN 16 (Flanged)".
_VARIANT: Final = re.compile(r"\s*\([^)]*\)\s*$")


@dataclass(frozen=True, slots=True)
class ProductGroups:
    """A document's chunks by product heading, with the chunks no heading clearly owns."""

    by_heading: dict[str, list[CatalogueChunk]]
    unclear: list[CatalogueChunk]
    types: dict[str, str]  # heading -> the product type it names

    def headings_of(self, product_type: str) -> list[str]:
        """The headings naming this product type, in reading order."""
        return [h for h in self.by_heading if self.types[h] == product_type]


def _section_types(
    section: str | None,
    matcher: ProductMatcher,
    vocabulary: SearchVocabulary,
    known: frozenset[str],
) -> set[str]:
    """The product types a section heading names."""
    return matcher.types_in(normalize_for_search(section, vocabulary), known) if section else set()


def group_by_product(
    chunks: Sequence[CatalogueChunk],
    ontology: Ontology,
    vocabulary: SearchVocabulary,
    overview_min_headings: int,
) -> ProductGroups:
    """Group one document's chunks by the product heading above them.

    Args:
        chunks: One document's chunks.
        ontology: Its product types and their words.
        vocabulary: Search vocabulary of the ontology.
        overview_min_headings: A page naming this many products is an overview.

    Returns:
        The groups. Chunks before the first heading, under a document-wide heading or on an
        overview page are unclear.
    """
    ordered = sorted(chunks, key=lambda c: (c.chunk.page_number, c.chunk.position))
    matcher = ProductMatcher.from_ontologies([ontology], vocabulary)
    known = frozenset(p.name for p in ontology.product_types)
    found = [_section_types(c.chunk.section, matcher, vocabulary, known) for c in ordered]
    on_page: dict[int, set[str]] = {}
    for item, types in zip(ordered, found, strict=True):
        if len(types) == 1:
            heading = _VARIANT.sub("", item.chunk.section or "")
            on_page.setdefault(item.chunk.page_number, set()).add(heading)
    by_heading: dict[str, list[CatalogueChunk]] = {}
    heading_types: dict[str, str] = {}
    unclear: list[CatalogueChunk] = []
    current: str | None = None
    for item, types in zip(ordered, found, strict=True):
        if len(on_page.get(item.chunk.page_number, ())) >= overview_min_headings:
            current = None
        elif len(types) == 1:
            current = _VARIANT.sub("", item.chunk.section or "")
            heading_types[current] = next(iter(types))
        elif types:
            current = None
        if current is None:
            unclear.append(item)
        else:
            by_heading.setdefault(current, []).append(item)
    return ProductGroups(by_heading, unclear, heading_types)


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
