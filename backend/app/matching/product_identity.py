"""Requirements that only name the product ("Valve Type : Globe Valve").

Such a line asks what the item is, and the catalogue was already chosen because it declares
that product type. The line is met by the catalogue's own heading for that product (the
chosen heading first), cited on the page it is printed on; with no such heading, by the
first printed line naming the product and no other. Every quote is verified on its page like
any other (CLAUDE.md section 7).

A value naming a sub-type ("Triple Offset Butterfly Valve") is met only by a product heading
naming that sub-type too (B2): a printed line never decides a sub-type. Words left after the
product and sub-type names ("metal seated") must each appear in a sub-heading under that same
product heading ("Replaceable Metal-to-Metal Seats"), which is cited as well; otherwise the
rule does not fire and the line stays for the judge.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import CatalogueChunk
from app.matching.comparator import MatchContext
from app.matching.headings import ProductGroups
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import Comparison, Evidence, LabelBasis
from app.schemas.requirements import Requirement
from app.tender.normalizer import stems


@dataclass(frozen=True, slots=True)
class ProductAsk:
    """What a product type line asks for, beyond the product type itself."""

    sub_types: frozenset[str]
    rest: frozenset[str]  # normalized search words left after the product and sub-type names

    @property
    def plain(self) -> bool:
        """True if the line names only the product."""
        return not self.sub_types and not self.rest


def product_ask(
    requirement: Requirement,
    product_type: str,
    ctx: MatchContext,
    label_words: Collection[str],
    generic_words: Collection[str] = (),
) -> ProductAsk | None:
    """What a line asking for the product type wants, or None if it is not such a line.

    Args:
        requirement: An atomic requirement.
        product_type: The item's product type.
        ctx: Ontology context; without an ontology nothing is named.
        label_words: Stemmed label words that ask for the type ("type", "product").
        generic_words: Stemmed words that name nothing ("valve", "of"), never left over.

    Returns:
        The sub-types and other words of the value, or None when the label does not ask for
        the type or the value does not name exactly this product type.
    """
    if ctx.ontology is None or ctx.vocabulary is None or requirement.label is None:
        return None
    if requirement.attribute is not None:
        return None
    if not set(stems(requirement.label)) & set(label_words):
        return None
    matcher = ProductMatcher.from_ontologies([ctx.ontology], ctx.vocabulary)
    value = normalize_for_search(requirement.value_text, ctx.vocabulary)
    if matcher.types_in(value, frozenset(matcher.types)) != {product_type}:
        return None
    sub_types = frozenset(matcher.sub_types_in(product_type, value))
    rest = value
    patterns = [*matcher.types[product_type]]
    patterns += [p for s in sub_types for p in matcher.sub_types[product_type][s]]
    for pattern in patterns:
        rest = pattern.sub(" ", rest)
    left = frozenset(rest.split()) - frozenset(generic_words)
    return ProductAsk(sub_types=sub_types, rest=left)


def names_only_product(
    requirement: Requirement,
    product_type: str,
    ctx: MatchContext,
    label_words: Collection[str],
    generic_words: Collection[str] = (),
) -> bool:
    """True if the line's label asks for the type and its value is just this product's name."""
    ask = product_ask(requirement, product_type, ctx, label_words, generic_words)
    return ask is not None and ask.plain


def _naming_line(
    chunk: CatalogueChunk, product_type: str, matcher: ProductMatcher, vocabulary: SearchVocabulary
) -> str | None:
    """The first printed line (a table row: cell) naming this product and no other, or None.

    "Gate, Globe and Check Valves" names three products, so it is not evidence for one. A table
    row's text joins header and cells, which the page never prints; each cell is printed.
    """
    known = frozenset(matcher.types)
    cells = [str(c) for c in chunk.chunk.meta.get("cells", [])]
    for line in cells or chunk.chunk.raw_text.splitlines():
        text = line.strip()
        if text and matcher.types_in(normalize_for_search(text, vocabulary), known) == {
            product_type
        }:
            return text
    return None


def _evidence(item: CatalogueChunk, label: str, text: str, page: int) -> Evidence:
    return Evidence(
        document=item.document.file,
        sha256=item.document.sha256,
        title=item.document.title,
        page_number=page,
        label=label,
        value=text,
        quote=text,
        quote_parts=[text],
        status=MatchStatus.FULL_MATCH,
        reason="the catalogue names the product the tender asks for",
    )


def _heading_page(item: CatalogueChunk) -> int:
    """The page a chunk's section heading is printed on."""
    page = item.chunk.meta.get("section_page")
    return page if isinstance(page, int) else item.chunk.page_number


def _sub_headings(
    chunks: Sequence[CatalogueChunk], wanted: frozenset[str], vocabulary: SearchVocabulary
) -> list[Evidence] | None:
    """Sub-headings under one product heading holding every wanted word; None if one is missing."""
    missing = set(wanted)
    found: list[Evidence] = []
    seen: set[str] = set()
    for item in chunks:
        section = item.chunk.section
        if not section or section in seen:
            continue
        seen.add(section)
        hit = missing & set(normalize_for_search(section, vocabulary).split())
        if hit:
            found.append(_evidence(item, "product heading", section, _heading_page(item)))
            missing -= hit
    return None if missing else found


def heading_evidence(
    ask: ProductAsk,
    groups: ProductGroups,
    headings: Sequence[str],
    product_type: str,
    ctx: MatchContext,
) -> list[Evidence] | None:
    """Evidence from the first product heading meeting the whole ask, or None.

    Args:
        ask: What the line asks for.
        groups: The document's chunks by product heading.
        headings: Headings of the item's product type, the chosen one first.
        product_type: The item's product type.
        ctx: Ontology context.

    Returns:
        The heading (and the sub-headings holding the other words), or None.
    """
    if ctx.ontology is None or ctx.vocabulary is None:
        return None
    matcher = ProductMatcher.from_ontologies([ctx.ontology], ctx.vocabulary)
    for heading in headings:
        chunks = groups.by_heading.get(heading, [])
        if not chunks:
            continue
        named = matcher.sub_types_in(product_type, normalize_for_search(heading, ctx.vocabulary))
        if not ask.sub_types <= named:
            continue
        own = chunks[0].chunk.section or heading
        rows = [_evidence(chunks[0], "product heading", own, _heading_page(chunks[0]))]
        more = _sub_headings(chunks[1:], ask.rest, ctx.vocabulary) if ask.rest else []
        if more is not None:
            return [*rows, *more]
    return None


def _identity(requirement: Requirement, evidence: list[Evidence], product_type: str) -> Comparison:
    return Comparison(
        requirement_id=requirement.id,
        status=MatchStatus.FULL_MATCH,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason=f"catalogue is for {product_type}, as asked",
        evidence=evidence,
    )


def product_identity(
    requirement: Requirement,
    chunks: Sequence[CatalogueChunk],
    product_type: str,
    ctx: MatchContext,
    ask: ProductAsk | None = None,
    groups: ProductGroups | None = None,
    headings: Sequence[str] = (),
) -> Comparison | None:
    """Full match citing the product heading or a line naming the product, or None.

    Args:
        requirement: A requirement for which `product_ask` is not None.
        chunks: The chosen product's chunks first, in reading order.
        product_type: The item's product type.
        ctx: Ontology context.
        ask: What the line asks for (None: only the product name).
        groups: The document's chunks by product heading, if known.
        headings: Headings of the item's product type, the chosen one first.

    Returns:
        The result (its quotes still to be verified), or None when nothing meets the ask.
    """
    if ctx.ontology is None or ctx.vocabulary is None:
        return None
    ask = ask or ProductAsk(sub_types=frozenset(), rest=frozenset())
    if groups is not None and headings:
        found = heading_evidence(ask, groups, headings, product_type, ctx)
        if found:
            return _identity(requirement, found, product_type)
    if not ask.plain:
        return None
    matcher = ProductMatcher.from_ontologies([ctx.ontology], ctx.vocabulary)
    for item in chunks:
        line = _naming_line(item, product_type, matcher, ctx.vocabulary)
        if line is not None:
            evidence = _evidence(item, "product", line, item.chunk.page_number)
            return _identity(requirement, [evidence], product_type)
    return None
