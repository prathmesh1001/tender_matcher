"""Match one tender item against every manufacturer (rules only, no LLM yet).

Per item: find its product type (ontology synonyms, or declared type names without one);
per manufacturer: evaluate each catalogue declaring the type on its own, keep the best
(see `documents`), and report every atomic requirement from that catalogue only. A
manufacturer without a catalogue for the type gets `unsupported` for every requirement.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import ChunkSource, declared_types_named
from app.matching.comparator import MatchContext, routed
from app.matching.documents import ItemQuery, SelectionOptions, evaluate_item
from app.matching.scorer import score
from app.retrieval.text_normalizer import normalize_for_search
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import Comparison, DocumentResult, LabelBasis, ManufacturerItemResult
from app.schemas.report import (
    CandidateSummary,
    ItemReport,
    ManufacturerItemReport,
    TiedDocumentRows,
)
from app.schemas.requirements import LineItem, Requirement
from app.tender.pipeline import product_types_named
from app.tender.splitter import leaves


@dataclass(frozen=True, slots=True)
class ItemInput:
    """A line item ready to match: its requirements and ontology context."""

    item: LineItem
    requirements: list[Requirement]
    ctx: MatchContext


def item_product_type(title: str, declared: Sequence[str], ctx: MatchContext) -> str | None:
    """The one declared product type the item title names, or None (none or several)."""
    if ctx.ontology is not None and ctx.vocabulary is not None:
        matcher = ProductMatcher.from_ontologies([ctx.ontology], ctx.vocabulary)
        wanted = frozenset(declared)
        found = matcher.types_in(normalize_for_search(title, ctx.vocabulary), wanted) or set(
            wanted.intersection(product_types_named(title, ctx.ontology))
        )
    else:
        found = declared_types_named(title, declared)
    return next(iter(found)) if len(found) == 1 else None


def _summary(result: DocumentResult) -> CandidateSummary:
    return CandidateSummary(
        document=result.document,
        sha256=result.sha256,
        title=result.title,
        sub_type_fit=result.sub_type_fit,
        critical_met=result.critical_met,
        critical_failed=result.critical_failed,
        product_heading=result.product_heading,
        fit_score=result.fit_score,
        decided=result.decided,
    )


def for_review(comparisons: Sequence[Comparison], note: str) -> list[Comparison]:
    """The results, each sent to review with a note saying why; statuses are unchanged.

    Args:
        comparisons: The results.
        note: Why a person must look.

    Returns:
        Copies with `needs_review` set and the note added to the reason.
    """
    return [
        c.model_copy(
            update={"needs_review": True, "reason": f"{c.reason}; {note}" if c.reason else note}
        )
        for c in comparisons
    ]


def _flagged(comparisons: Sequence[Comparison], tied: Sequence[str]) -> list[Comparison]:
    """A tied catalogue's rows, each for review and saying that no catalogue was picked."""
    return for_review(comparisons, f"catalogues tie, none picked: {', '.join(tied)}")


def _no_catalogue(
    requirements: Sequence[Requirement], product_type: str | None
) -> list[Comparison]:
    """Unsupported for every requirement: the manufacturer has no catalogue for the type."""
    reason = (
        f"no catalogue declares {product_type}" if product_type else "item product type unknown"
    )
    return [
        Comparison(
            requirement_id=r.id,
            status=MatchStatus.UNSUPPORTED,
            needs_review=True,
            decided_by=DecisionSource.NONE,
            basis=LabelBasis.NONE,
            reason=reason,
        )
        for r in requirements
    ]


def general_result(requirement: Requirement) -> Comparison:
    """The result of a requirement outside the item schedule.

    Args:
        requirement: A general requirement.

    Returns:
        Its routed result; one that catalogues could prove is free text with no product to
        look in, so it waits for the LLM judge.
    """
    return routed(requirement) or Comparison(
        requirement_id=requirement.id,
        status=MatchStatus.UNSUPPORTED,
        needs_review=True,
        decided_by=DecisionSource.NONE,
        basis=LabelBasis.NONE,
        reason="free text: needs the LLM judge (not enabled)",
    )


def manufacturer_report(
    result: ManufacturerItemResult, requirements: Sequence[Requirement], options: SelectionOptions
) -> ManufacturerItemReport:
    """Report one manufacturer from its chosen catalogue only.

    Tied catalogues score the same on every ranking step, so the score is theirs together and
    each one's rows are reported; none is picked.
    """
    chosen = next((d for d in result.documents if d.sha256 == result.chosen), None)
    tied = result.tied_documents
    tied_rows = [
        TiedDocumentRows(document=d.document, results=_flagged(d.comparisons, tied))
        for d in result.documents
        if d.document in tied
    ]
    if tied_rows:
        comparisons = tied_rows[0].results
    elif chosen:
        comparisons = chosen.comparisons
    else:
        comparisons = _no_catalogue(requirements, result.product_type)
    return ManufacturerItemReport(
        manufacturer=result.manufacturer,
        chosen=_summary(chosen) if chosen else None,
        candidates=[_summary(d) for d in result.documents],
        tied_documents=tied,
        tied_rows=tied_rows,
        results=list(comparisons),
        score=score(comparisons, options.status_scores),
    )


def item_for_review(report: ItemReport, note: str, options: SelectionOptions) -> ItemReport:
    """One item with every manufacturer's results sent to review (scores count them).

    Args:
        report: The item's report.
        note: Why a person must look.
        options: Holds the status scores.

    Returns:
        A copy with results, tied rows and scores updated.
    """
    manufacturers: list[ManufacturerItemReport] = []
    for m in report.manufacturers:
        results = for_review(m.results, note)
        tied_rows = [
            row.model_copy(update={"results": for_review(row.results, note)}) for row in m.tied_rows
        ]
        manufacturers.append(
            m.model_copy(
                update={
                    "results": results,
                    "tied_rows": tied_rows,
                    "score": score(results, options.status_scores),
                }
            )
        )
    return report.model_copy(update={"manufacturers": manufacturers})


def match_item(entry: ItemInput, source: ChunkSource, options: SelectionOptions) -> ItemReport:
    """Match one item against every manufacturer of the categories declaring its type.

    Args:
        entry: The item, its requirements and ontology context.
        source: Catalogue chunks and page text.
        options: Comparator, scoring and verification settings.

    Returns:
        The item report; manufacturers sorted by name.
    """
    declared = source.product_types()
    product_type = item_product_type(entry.item.title, sorted(declared), entry.ctx)
    categories = sorted(declared.get(product_type, frozenset())) if product_type else []
    every = {
        m
        for c in {cat for cats in declared.values() for cat in cats}
        for m in source.manufacturers(c)
    }
    atomic = leaves(entry.requirements)
    reports: list[ManufacturerItemReport] = []
    sub_type: str | None = None
    for manufacturer in sorted(every):
        chunks = [
            chunk
            for category in categories
            for chunk in source.chunks_for_product(
                manufacturer, category, product_type or "", use_tags=entry.ctx.ontology is not None
            )
        ]
        query = ItemQuery(manufacturer, entry.item.item_no, entry.item.title, product_type)
        result = evaluate_item(query, atomic, chunks, entry.ctx, options, source)
        sub_type = sub_type or result.item_sub_type
        reports.append(manufacturer_report(result, atomic, options))
    return ItemReport(
        item=entry.item,
        category=entry.ctx.ontology.category if entry.ctx.ontology else None,
        product_type=product_type,
        sub_type=sub_type,
        requirements=entry.requirements,
        manufacturers=reports,
    )
