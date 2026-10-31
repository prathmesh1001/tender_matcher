"""Per item x catalogue document: evaluate each document on its own, then choose one.

A manufacturer's documents are never pooled: specs of one catalogue (Aquaseal, concentric)
must not decide a requirement of another (triple offset), CLAUDE.md B10. Each document
declaring the item's product type is compared separately; the best document is the one
whose declared sub-type fits the item (B2: match > general/unknown > mismatch), then the
higher fit score (Σ status score over decided requirements), then more decided
requirements. Choosing a model or version column inside the document is a later step.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings
from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import CatalogueChunk, CatalogueDocument, declared_types_named
from app.matching.comparator import ComparatorOptions, MatchContext, compare
from app.matching.evidence_verifier import PageTexts, verify_comparison
from app.matching.fields import located_fields
from app.retrieval.text_normalizer import normalize_for_search
from app.schemas.enums import MatchStatus
from app.schemas.matching import Comparison, DocumentResult, ManufacturerItemResult, SubTypeFit
from app.schemas.requirements import Requirement

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

    @classmethod
    def from_settings(cls, settings: Settings) -> SelectionOptions:
        """Build options from application settings."""
        return cls(
            comparator=ComparatorOptions.from_settings(settings),
            status_scores={MatchStatus(k): v for k, v in settings.STATUS_SCORES.items()},
            quote_threshold=settings.QUOTE_MATCH_THRESHOLD,
            min_coverage_pct=settings.RANKING_MIN_COVERAGE_PCT,
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


def evaluate_document(
    requirements: Sequence[Requirement],
    document: CatalogueDocument,
    chunks: Sequence[CatalogueChunk],
    fit: SubTypeFit,
    ctx: MatchContext,
    options: SelectionOptions,
    pages: PageTexts,
) -> DocumentResult:
    """Compare every requirement with one document's chunks only, quotes verified.

    Verification runs before scoring, so an unverified match never helps a document win.
    """
    fields = located_fields(chunks, options.comparator.fields)
    comparisons = [
        verify_comparison(
            compare(r, fields, ctx, options.comparator), pages, options.quote_threshold
        )
        for r in requirements
    ]
    score, decided = _fit(comparisons, options.status_scores)
    return DocumentResult(
        document=document.file,
        sha256=document.sha256,
        title=document.title,
        declared_sub_types=list(document.sub_types),
        sub_type_fit=fit,
        fit_score=score,
        decided=decided,
        comparisons=comparisons,
    )


def rank_key(result: DocumentResult) -> tuple[int, float, int, str]:
    """Sort key, best first: sub-type fit, fit score, decided count, then file name."""
    return (-_FIT_RANK[result.sub_type_fit], -result.fit_score, -result.decided, result.document)


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
        Candidates best first; `chosen` is the first one's sha256 (None without candidates).
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
            )
            for doc, docs_chunks in groups
        ),
        key=rank_key,
    )
    return ManufacturerItemResult(
        manufacturer=query.manufacturer,
        item_no=query.item_no,
        product_type=query.product_type,
        item_sub_type=wanted,
        chosen=results[0].sha256 if results else None,
        documents=results,
    )
