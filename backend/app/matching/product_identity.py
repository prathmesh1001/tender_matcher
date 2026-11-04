"""Requirements that only name the product ("Valve Type : Globe Valve").

Such a line asks what the item is, and the catalogue was already chosen because it declares
that product type. The line is met when the chosen catalogue itself prints the product's name:
the evidence is that printed line, verified on its page like any other quote (CLAUDE.md
section 7). The rule never fires when the value says more than the product name ("Globe Valve,
Bolted Bonnet") or names a sub-type ("Triple Offset Butterfly Valve"): a sub-type needs the
sub-type checks (B2), and extra words are other requirements.
"""

from collections.abc import Collection, Sequence

from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import CatalogueChunk
from app.matching.comparator import MatchContext
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import Comparison, Evidence, LabelBasis
from app.schemas.requirements import Requirement
from app.tender.normalizer import stems


def names_only_product(
    requirement: Requirement,
    product_type: str,
    ctx: MatchContext,
    label_words: Collection[str],
) -> bool:
    """True if the line's label asks for the type and its value is just this product's name.

    Args:
        requirement: An atomic requirement.
        product_type: The item's product type.
        ctx: Ontology context; without an ontology nothing is named.
        label_words: Stemmed label words that ask for the type ("type", "product").

    Returns:
        Whether the product identity rule applies.
    """
    if ctx.ontology is None or ctx.vocabulary is None or requirement.label is None:
        return False
    if requirement.attribute is not None:
        return False
    if not set(stems(requirement.label)) & set(label_words):
        return False
    matcher = ProductMatcher.from_ontologies([ctx.ontology], ctx.vocabulary)
    value = normalize_for_search(requirement.value_text, ctx.vocabulary)
    known = frozenset(matcher.types)
    if matcher.types_in(value, known) != {product_type}:
        return False
    if matcher.sub_types_in(product_type, value):
        return False
    rest = value
    for pattern in matcher.types[product_type]:
        rest = pattern.sub(" ", rest)
    return not rest.split()


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


def product_identity(
    requirement: Requirement,
    chunks: Sequence[CatalogueChunk],
    product_type: str,
    ctx: MatchContext,
) -> Comparison | None:
    """Full match citing the catalogue line that names the product, or None if none does.

    Args:
        requirement: A requirement for which `names_only_product` is true.
        chunks: The chosen product's chunks first, in reading order.
        product_type: The item's product type.
        ctx: Ontology context.

    Returns:
        The result (its quote still to be verified), or None when no line names the product.
    """
    if ctx.ontology is None or ctx.vocabulary is None:
        return None
    matcher = ProductMatcher.from_ontologies([ctx.ontology], ctx.vocabulary)
    for item in chunks:
        line = _naming_line(item, product_type, matcher, ctx.vocabulary)
        if line is None:
            continue
        evidence = Evidence(
            document=item.document.file,
            sha256=item.document.sha256,
            title=item.document.title,
            page_number=item.chunk.page_number,
            label="product",
            value=line,
            quote=line,
            quote_parts=[line],
            status=MatchStatus.FULL_MATCH,
            reason="the catalogue names the product the tender asks for",
        )
        return Comparison(
            requirement_id=requirement.id,
            status=MatchStatus.FULL_MATCH,
            needs_review=False,
            decided_by=DecisionSource.RULE,
            basis=LabelBasis.ATTRIBUTE,
            reason=f"catalogue is for {product_type}, as asked",
            evidence=[evidence],
        )
    return None
