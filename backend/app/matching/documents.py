"""Per item x catalogue document: evaluate each document on its own, then choose one.

A manufacturer's documents are never pooled: specs of one catalogue (Aquaseal, concentric)
must not decide a requirement of another (triple offset), CLAUDE.md B10. Each document
declaring the item's product type is compared separately. The best document is chosen by what
the product is before how much is known about it: its declared sub-type fits the item (B2:
match > general/unknown > mismatch), then the most critical requirements met and the fewest
contradicted (attributes the ontology marks `critical`: a catalogue of the wrong standard or
size is the wrong catalogue, however much else it states), then coverage (more decided
requirements), then the higher fit score (Σ status score). A document holding several models of
the item's product is read from the best of them (`_by_heading`); this is no full model lock, and
choosing a version column inside the document is a later step.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings
from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import CatalogueChunk, CatalogueDocument, declared_types_named
from app.matching.comparator import (
    ComparatorOptions,
    MatchContext,
    compare,
    judge_numeric,
    offered_as_option,
)
from app.matching.evidence_verifier import PageTexts, verify_comparison
from app.matching.fields import located_fields, option_fields
from app.matching.headings import ProductGroups, group_by_product
from app.matching.product_identity import names_only_product, product_identity
from app.retrieval.text_normalizer import normalize_for_search, stem_token
from app.schemas.enums import DecisionSource, MatchStatus, ReviewFlag
from app.schemas.matching import Comparison, DocumentResult, ManufacturerItemResult, SubTypeFit
from app.schemas.requirements import Requirement, ValueKind

_FIT_RANK: Final = {
    SubTypeFit.MATCH: 2,
    SubTypeFit.GENERAL: 1,
    SubTypeFit.UNKNOWN: 1,
    SubTypeFit.MISMATCH: 0,
}


@dataclass(frozen=True, slots=True)
class SelectionOptions:
    """Comparator settings plus the score of each decided status."""

    comparator: ComparatorOptions
    status_scores: dict[MatchStatus, float]
    quote_threshold: float
    min_coverage_pct: float
    overview_min_headings: int = 3
    product_label_words: frozenset[str] = frozenset()

    @classmethod
    def from_settings(cls, settings: Settings) -> SelectionOptions:
        """Build options from application settings."""
        return cls(
            comparator=ComparatorOptions.from_settings(settings),
            status_scores={MatchStatus(k): v for k, v in settings.STATUS_SCORES.items()},
            quote_threshold=settings.QUOTE_MATCH_THRESHOLD,
            min_coverage_pct=settings.RANKING_MIN_COVERAGE_PCT,
            overview_min_headings=settings.CATALOGUE_OVERVIEW_MIN_HEADINGS,
            product_label_words=frozenset(stem_token(w) for w in settings.PRODUCT_TYPE_LABEL_WORDS),
        )


def _product_sub_types(ctx: MatchContext, product_type: str) -> frozenset[str] | None:
    """Sub-type names of the product in the ontology, or None without an ontology."""
    if ctx.ontology is None:
        return None
    product = ctx.ontology.product_type(product_type)
    return frozenset(s.name for s in product.sub_types) if product else frozenset()


def item_sub_type(
    title: str, product_type: str, ctx: MatchContext, declared: Sequence[str]
) -> str | None:
    """The sub-type the item title names, if exactly one.

    With an ontology its sub-type synonyms are used ("TRIPLE OFFSET", "TOBV"); without one,
    only sub-type names declared by the candidate documents ("triple_offset").

    Args:
        title: Tender item title.
        product_type: The item's product type.
        ctx: Ontology context (may be empty).
        declared: Sub-types declared by the candidate documents.

    Returns:
        The sub-type, or None if the title names none or several.
    """
    if ctx.ontology is not None and ctx.vocabulary is not None:
        matcher = ProductMatcher.from_ontologies([ctx.ontology], ctx.vocabulary)
        found = matcher.sub_types_in(product_type, normalize_for_search(title, ctx.vocabulary))
    else:
        found = declared_types_named(title, declared)
    return next(iter(found)) if len(found) == 1 else None


def sub_type_fit(
    document: CatalogueDocument, product_type: str, wanted: str | None, ctx: MatchContext
) -> SubTypeFit:
    """How the document's declared sub-types fit the item's sub-type.

    With an ontology only sub-types of this product count (a "dual_plate_check" declared
    for a document's check valves says nothing about its butterfly valves).
    """
    if wanted is None:
        return SubTypeFit.UNKNOWN
    of_product = _product_sub_types(ctx, product_type)
    declared = {s for s in document.sub_types if of_product is None or s in of_product}
    if not declared:
        return SubTypeFit.GENERAL
    return SubTypeFit.MATCH if wanted in declared else SubTypeFit.MISMATCH


def by_document(
    chunks: Sequence[CatalogueChunk],
) -> list[tuple[CatalogueDocument, list[CatalogueChunk]]]:
    """Chunks grouped by their document, in first-seen order."""
    groups: dict[str, tuple[CatalogueDocument, list[CatalogueChunk]]] = {}
    for chunk in chunks:
        groups.setdefault(chunk.document.sha256, (chunk.document, []))[1].append(chunk)
    return list(groups.values())


def _fit(comparisons: Sequence[Comparison], scores: dict[MatchStatus, float]) -> tuple[float, int]:
    """(Σ status score, number decided) over the decided comparisons."""
    decided = [c for c in comparisons if c.status in scores]
    return sum(scores[c.status] for c in decided), len(decided)


def _is_critical(requirement: Requirement, ctx: MatchContext) -> bool:
    """True if the ontology marks the requirement's attribute as identifying the product."""
    attribute = ctx.attribute(requirement)
    return attribute is not None and attribute.critical


def _heading_critical(
    requirements: Sequence[Requirement],
    found: Sequence[Comparison],
    heading: str,
    ctx: MatchContext,
    options: SelectionOptions,
) -> tuple[float, int]:
    """Critical requirements a heading's product meets and fails.

    A heading can print what its fields leave out ("Aquaseal 16 ... - PN 16"). That helps choose
    the product and is never evidence: it decides no requirement.
    """
    met, failed = 0.0, 0
    for requirement, comparison in zip(requirements, found, strict=True):
        if not _is_critical(requirement, ctx):
            continue
        status = comparison.status
        if status not in options.status_scores and requirement.kind is ValueKind.NUMERIC:
            printed = judge_numeric(requirement, heading, False, ctx, options.comparator)
            status = printed.status or status
        met += options.status_scores.get(status, 0.0)
        failed += status is MatchStatus.MISSING_CAPABILITY
    return met, failed


def _chosen_heading(
    requirements: Sequence[Requirement],
    groups: ProductGroups,
    product_type: str,
    ctx: MatchContext,
    options: SelectionOptions,
    pages: PageTexts,
) -> str | None:
    """The heading of the item's product type that best meets it; None if tied or none decides.

    The order is the document's own: critical requirements met, fewest contradicted, coverage, fit.
    """
    ranked: list[tuple[tuple[float, int, int, float], str]] = []
    for heading in groups.headings_of(product_type):
        fields = located_fields(groups.by_heading[heading], options.comparator.fields)
        found = [
            verify_comparison(
                compare(r, fields, ctx, options.comparator), pages, options.quote_threshold
            )
            for r in requirements
        ]
        score, decided = _fit(found, options.status_scores)
        met, failed = _heading_critical(requirements, found, heading, ctx, options)
        ranked.append(((met, -failed, decided, score), heading))
    ranked.sort(key=lambda r: r[0], reverse=True)
    if not ranked or ranked[0][0][2] == 0 or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return None
    return ranked[0][1]


def _capped(comparison: Comparison) -> Comparison:
    """Evidence no heading clearly owns can't be full: partial + review."""
    flags = [*comparison.flags, ReviewFlag.HEADING_UNCLEAR]
    if comparison.status is not MatchStatus.FULL_MATCH:
        return comparison.model_copy(update={"needs_review": True, "flags": flags})
    reason = "product heading unclear: capped at partial"
    return comparison.model_copy(
        update={
            "status": MatchStatus.PARTIAL_MATCH,
            "needs_review": True,
            "reason": reason,
            "flags": flags,
        }
    )


def _from_another_product(comparison: Comparison, chosen: str | None) -> Comparison:
    """Evidence from a sibling product says nothing about this one: unsupported + review."""
    reason = f"stated only for another product in this document, not {chosen}"
    return comparison.model_copy(
        update={
            "status": MatchStatus.UNSUPPORTED,
            "needs_review": True,
            "decided_by": DecisionSource.NONE,
            "reason": reason,
        }
    )


def _by_heading(
    requirement: Requirement,
    groups: ProductGroups,
    chosen: str | None,
    one_product: bool,
    ctx: MatchContext,
    options: SelectionOptions,
) -> Comparison:
    """Decide from the chosen product, then from what no heading owns, then report the rest.

    In a document about `one_product`, pages with no heading belong to that product.
    """
    fields = options.comparator.fields
    decides = options.status_scores
    own_chunks = groups.by_heading[chosen] if chosen is not None else []
    if one_product:
        own_chunks = [*own_chunks, *groups.unclear]
    if own_chunks:
        result = compare(requirement, located_fields(own_chunks, fields), ctx, options.comparator)
        if result.status in decides:
            return result
    other = [c for h, chunks in groups.by_heading.items() if h != chosen for c in chunks]
    # With no chosen product nothing can be told apart: all of it is unclear.
    open_chunks = groups.unclear if chosen is not None else [*groups.unclear, *other]
    result = compare(requirement, located_fields(open_chunks, fields), ctx, options.comparator)
    if result.status in decides:
        return _capped(result)
    whole = located_fields([*groups.unclear, *other], fields)
    result = compare(requirement, whole, ctx, options.comparator)
    return _from_another_product(result, chosen) if result.status in decides else result


def _untagged_capped(
    requirements: Sequence[Requirement],
    raw: Sequence[Comparison],
    chunks: Sequence[CatalogueChunk],
    product_type: str,
    ctx: MatchContext,
    options: SelectionOptions,
) -> list[Comparison]:
    """In a document about several products, a full match needs a chunk tagged with the item's.

    An untagged chunk there may be about any of them (a cover page "gate, globe, and check NPS 2
    - 64", a gate assembly step), so a full match only untagged chunks give is capped at partial.
    Nothing else changes: chunks are not reordered, so no new failure can appear. A full match
    the tagged chunks give is cited from them, not from a cover page saying the same.
    """
    own = [c for c in chunks if c.chunk.product_type == product_type]
    tagged = located_fields(own, options.comparator.fields)
    out: list[Comparison] = []
    for requirement, result in zip(requirements, raw, strict=True):
        if result.status is MatchStatus.FULL_MATCH:
            alone = compare(requirement, tagged, ctx, options.comparator)
            result = alone if alone.status is MatchStatus.FULL_MATCH else _capped(result)
        out.append(result)
    return out


def _reading_order(
    chunks: Sequence[CatalogueChunk],
    groups: ProductGroups | None,
    chosen: str | None,
    product_type: str | None,
) -> list[CatalogueChunk]:
    """The chosen product's chunks, then those under any heading of the type, then the rest.

    A contents page lists every product, so chunks under the product's own headings come first.
    """
    own: list[CatalogueChunk] = []
    if groups is not None:
        headings = [chosen] if chosen is not None else []
        headings += [h for h in groups.headings_of(product_type or "") if h != chosen]
        own = [c for h in headings for c in groups.by_heading[h]]
    seen = {id(c) for c in own}
    rest = [c for c in chunks if id(c) not in seen]
    return [*own, *sorted(rest, key=lambda c: (c.chunk.page_number, c.chunk.position))]


def _identified(
    requirements: Sequence[Requirement],
    raw: Sequence[Comparison],
    chunks: Sequence[CatalogueChunk],
    product_type: str | None,
    ctx: MatchContext,
    options: SelectionOptions,
) -> list[Comparison]:
    """Undecided lines that only name the product, met by the catalogue line naming it."""
    if not product_type or not options.product_label_words:
        return list(raw)
    found: list[Comparison] = []
    for requirement, comparison in zip(requirements, raw, strict=True):
        if comparison.status is MatchStatus.UNSUPPORTED and names_only_product(
            requirement, product_type, ctx, options.product_label_words
        ):
            comparison = product_identity(requirement, chunks, product_type, ctx) or comparison
        found.append(comparison)
    return found


def evaluate_document(
    requirements: Sequence[Requirement],
    document: CatalogueDocument,
    chunks: Sequence[CatalogueChunk],
    fit: SubTypeFit,
    ctx: MatchContext,
    options: SelectionOptions,
    pages: PageTexts,
    product_type: str | None = None,
) -> DocumentResult:
    """Compare every requirement with one document's chunks only, quotes verified.

    Verification runs before scoring, so an unverified match never helps a document win. A document
    with several headings of the item's product type is read through `_by_heading`.
    """
    groups = (
        group_by_product(chunks, ctx.ontology, ctx.vocabulary, options.overview_min_headings)
        if ctx.ontology is not None and ctx.vocabulary is not None and product_type
        else None
    )
    several = groups is not None and len(groups.headings_of(product_type or "")) > 1
    chosen = None
    if groups is not None and several:
        chosen = _chosen_heading(requirements, groups, product_type or "", ctx, options, pages)
        one_product = len(document.product_types) == 1 and set(groups.types.values()) == {
            product_type
        }
        raw = [_by_heading(r, groups, chosen, one_product, ctx, options) for r in requirements]
    else:
        fields = located_fields(chunks, options.comparator.fields)
        raw = [compare(r, fields, ctx, options.comparator) for r in requirements]
        if product_type and len(document.product_types) > 1:
            raw = _untagged_capped(requirements, raw, chunks, product_type, ctx, options)
    ordered = _reading_order(chunks, groups, chosen, product_type)
    raw = _identified(requirements, raw, ordered, product_type, ctx, options)
    offers = option_fields(chunks, options.comparator.fields)
    softened = [
        offered_as_option(r, c, offers, ctx, options.comparator)
        for r, c in zip(requirements, raw, strict=True)
    ]
    comparisons = [verify_comparison(c, pages, options.quote_threshold) for c in softened]
    score, decided = _fit(comparisons, options.status_scores)
    critical = [c for r, c in zip(requirements, comparisons, strict=True) if _is_critical(r, ctx)]
    return DocumentResult(
        document=document.file,
        sha256=document.sha256,
        title=document.title,
        declared_sub_types=list(document.sub_types),
        sub_type_fit=fit,
        critical_met=_fit(critical, options.status_scores)[0],
        critical_failed=sum(1 for c in critical if c.status is MatchStatus.MISSING_CAPABILITY),
        fit_score=score,
        decided=decided,
        product_heading=chosen,
        comparisons=comparisons,
    )


def rank_key(result: DocumentResult) -> tuple[int, bool, float, int, int, float]:
    """Sort key, best first: identity, then coverage, then fit. Equal keys are a tie.

    Identity is the product (its sub-type fit), then the critical requirements met, then the
    fewest it contradicts: a catalogue stating another standard is further from the item
    than one that states none. A catalogue deciding nothing never wins on that alone, so it
    comes after any that decides something. Decided count is coverage, as every candidate has
    the same requirements.
    """
    return (
        -_FIT_RANK[result.sub_type_fit],
        result.decided == 0,
        -result.critical_met,
        result.critical_failed,
        -result.decided,
        -result.fit_score,
    )


@dataclass(frozen=True, slots=True)
class ItemQuery:
    """What is being matched: one tender item of one product type, for one manufacturer."""

    manufacturer: str
    item_no: str
    title: str
    product_type: str | None


def evaluate_item(
    query: ItemQuery,
    requirements: Sequence[Requirement],
    chunks: Sequence[CatalogueChunk],
    ctx: MatchContext,
    options: SelectionOptions,
    pages: PageTexts,
) -> ManufacturerItemResult:
    """Evaluate each candidate document for one item and choose the best one.

    Args:
        query: Manufacturer, item and its product type (None: no document can be chosen).
        requirements: The item's atomic requirements.
        chunks: The manufacturer's chunks for the product type (all candidate documents).
        ctx: Ontology context (may be empty).
        options: Comparator settings, status scores and quote threshold.
        pages: Page text of the catalogues, for quote verification.

    Returns:
        Candidates best first; `chosen` is the first one's sha256. None without candidates, or if
        the best ones tie: nothing tells them apart, so they are listed and a reader decides.
    """
    groups = by_document(chunks) if query.product_type else []
    declared = sorted({s for doc, _ in groups for s in doc.sub_types})
    wanted = (
        item_sub_type(query.title, query.product_type, ctx, declared)
        if query.product_type
        else None
    )
    results = sorted(
        (
            evaluate_document(
                requirements,
                doc,
                docs_chunks,
                sub_type_fit(doc, query.product_type or "", wanted, ctx),
                ctx,
                options,
                pages,
                query.product_type,
            )
            for doc, docs_chunks in groups
        ),
        key=rank_key,
    )
    tied = [r.document for r in results if rank_key(r) == rank_key(results[0])]
    return ManufacturerItemResult(
        manufacturer=query.manufacturer,
        item_no=query.item_no,
        product_type=query.product_type,
        item_sub_type=wanted,
        chosen=results[0].sha256 if len(tied) == 1 else None,
        documents=results,
        tied_documents=tied if len(tied) > 1 else [],
    )
