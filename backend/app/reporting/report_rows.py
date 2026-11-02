"""A finished report as database rows. Pure functions: nothing here touches a session.

Ids are set here so rows can point at each other before anything is written. Requirement ids
are only unique within one item (a tender can repeat its item numbers), so results are linked
through their own item's requirements, never through a tender-wide lookup.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.db.models import Evidence, LineItem, MatchResult, Requirement
from app.schemas.enums import MatchStatus
from app.schemas.matching import Comparison
from app.schemas.matching import Evidence as EvidenceField
from app.schemas.report import ItemReport, ManufacturerItemReport, TenderReport
from app.schemas.requirements import Requirement as RequirementSchema

# Tenders print no weights yet, so every requirement counts the same (see `matching/scorer`).
_WEIGHT: Final = 1.0


@dataclass(frozen=True, slots=True)
class RowContext:
    """What rows need beyond the report itself."""

    run_id: uuid.UUID
    document_ids: Mapping[str, uuid.UUID]
    status_scores: Mapping[MatchStatus, float]


@dataclass(slots=True)
class ReportRows:
    """Every row one report gives, in the order they must be written."""

    line_items: list[LineItem] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    results: list[MatchResult] = field(default_factory=list)


def _all_comparisons(report: TenderReport) -> Iterable[Comparison]:
    for item in report.items:
        for manufacturer in item.manufacturers:
            yield from manufacturer.results
            for tied in manufacturer.tied_rows:
                yield from tied.results
    yield from report.general_results


def cited_hashes(report: TenderReport) -> set[str]:
    """The sha256 of every catalogue the report cites.

    Args:
        report: The report.

    Returns:
        The hashes, each of which needs a stored document.
    """
    return {e.sha256 for c in _all_comparisons(report) for e in c.evidence}


def _requirement_rows(
    requirements: Sequence[RequirementSchema],
    ctx: RowContext,
    line_item_id: uuid.UUID | None,
    first_position: int,
) -> dict[str, Requirement]:
    """Rows of one group of requirements (one item, or the general ones), by their report id."""
    rows: dict[str, Requirement] = {}
    for offset, r in enumerate(requirements):
        parent = rows.get(r.parent_id) if r.parent_id else None
        rows[r.id] = Requirement(
            id=uuid.uuid7(),
            run_id=ctx.run_id,
            line_item_id=line_item_id,
            parent_id=parent.id if parent else None,
            ref=r.id,
            position=first_position + offset,
            document=r.document,
            page_number=r.page_number,
            raw_text=r.raw_text,
            label=r.label,
            req_type=r.req_type,
            category=r.category,
            evidence=r.evidence,
            product_type=r.product_type,
            mandatory=r.mandatory,
            named_make=r.named_make,
            single_firm=r.single_firm,
            acceptable_makes=r.acceptable_makes,
            or_equivalent=r.or_equivalent,
            flags=r.flags,
            kind=r.kind,
            attribute=r.attribute,
            operator=r.operator,
            value_text=r.value_text,
            value_num=r.value_num,
            value_min=r.value_min,
            value_max=r.value_max,
            unit=r.unit,
            canonical_unit=r.canonical_unit,
            alternatives=r.alternatives,
            any_alternative=r.any_alternative,
            weight=_WEIGHT,
        )
    return rows


def _evidence_row(evidence: EvidenceField, ctx: RowContext) -> Evidence:
    return Evidence(
        document_id=ctx.document_ids[evidence.sha256],
        page_number=evidence.page_number,
        label=evidence.label,
        value=evidence.value,
        quote=evidence.quote,
        quote_parts=evidence.quote_parts,
        status=evidence.status,
        reason=evidence.reason,
        verified=evidence.verified,
        match_score=evidence.match_score,
    )


@dataclass(frozen=True, slots=True)
class _Source:
    """Who a result row speaks for: its manufacturer, catalogue and product."""

    manufacturer: str | None = None
    catalogue: str | None = None
    product_family: str | None = None
    tied: bool = False


def _result_row(
    comparison: Comparison, requirement: Requirement, source: _Source, ctx: RowContext
) -> MatchResult:
    return MatchResult(
        run_id=ctx.run_id,
        requirement_id=requirement.id,
        manufacturer=source.manufacturer,
        catalogue=source.catalogue,
        product_family=source.product_family,
        tied=source.tied,
        status=comparison.status,
        score=ctx.status_scores.get(comparison.status),
        capability=comparison.capability,
        needs_review=comparison.needs_review,
        confidence=comparison.confidence,
        decided_by=comparison.decided_by,
        basis=comparison.basis,
        reasoning=comparison.reason,
        evidence=[_evidence_row(e, ctx) for e in comparison.evidence],
    )


def _manufacturer_rows(
    m: ManufacturerItemReport, requirements: Mapping[str, Requirement], ctx: RowContext
) -> list[MatchResult]:
    """One manufacturer's rows for one item: each tied catalogue's own, else the chosen one's.

    On a tie `m.results` only repeats the first tied catalogue, so it is not stored: that would
    read as if a catalogue had been picked.
    """
    if m.tied_rows:
        return [
            _result_row(
                c,
                requirements[c.requirement_id],
                _Source(m.manufacturer, tied.document, tied=True),
                ctx,
            )
            for tied in m.tied_rows
            for c in tied.results
        ]
    source = _Source(
        m.manufacturer,
        m.chosen.document if m.chosen else None,
        m.chosen.product_heading if m.chosen else None,
    )
    return [_result_row(c, requirements[c.requirement_id], source, ctx) for c in m.results]


def _item_rows(item: ItemReport, position: int, rows: ReportRows, ctx: RowContext) -> None:
    """Add one item's line item, requirements and results to `rows`."""
    line_item = LineItem(
        id=uuid.uuid7(),
        run_id=ctx.run_id,
        position=position,
        item_no=item.item.item_no,
        title=item.item.title,
        description_lines=item.item.description_lines,
        description_header=item.item.description_header,
        quantity=item.item.quantity,
        unit=item.item.unit,
        document=item.item.document,
        page_number=item.item.page_number,
        category=item.category,
        product_type=item.product_type,
        sub_type=item.sub_type,
    )
    rows.line_items.append(line_item)
    requirements = _requirement_rows(
        item.requirements, ctx, line_item.id, first_position=len(rows.requirements)
    )
    rows.requirements.extend(requirements.values())
    for manufacturer in item.manufacturers:
        rows.results.extend(_manufacturer_rows(manufacturer, requirements, ctx))


def report_rows(report: TenderReport, ctx: RowContext) -> ReportRows:
    """Turn a report into rows.

    Args:
        report: The finished report.
        ctx: The run these rows belong to, stored catalogues by sha256, and status scores.

    Returns:
        Line items, requirements (items first, then general ones) and results.

    Raises:
        KeyError: If a cited catalogue is missing from `ctx.document_ids`.
    """
    rows = ReportRows()
    for position, item in enumerate(report.items):
        _item_rows(item, position, rows, ctx)
    general = _requirement_rows(
        report.general_requirements, ctx, None, first_position=len(rows.requirements)
    )
    rows.requirements.extend(general.values())
    rows.results.extend(
        _result_row(c, general[c.requirement_id], _Source(), ctx) for c in report.general_results
    )
    return rows
