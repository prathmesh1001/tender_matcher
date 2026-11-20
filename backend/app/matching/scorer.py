"""Scores and ranking (CLAUDE.md section 7). Pure functions.

Per manufacturer: compatibility % = Σ(score * weight) / Σ(weight) over decided requirements
(full 1.0, partial 0.5, missing 0.0 by config); unsupported requirements are excluded and
show up in coverage % = decided / all atomic requirements instead (not_applicable ones do not
count at all). Every requirement weighs
1 for now (no weights are printed in tenders yet). Ranking: manufacturers below the minimum
coverage are marked and listed after the others, so a high score on little evidence never
leads; within each group by compatibility, then coverage, with nothing decided last. The same
order picks each item's top vendors (`top_vendors`), on that item's results alone.
"""

from collections.abc import Mapping, Sequence

from app.schemas.enums import MatchStatus, VendorSource
from app.schemas.matching import Comparison
from app.schemas.report import ItemVendor, ManufacturerItemReport, RankingEntry, Score

_PERCENT = 100.0


def score(comparisons: Sequence[Comparison], status_scores: Mapping[MatchStatus, float]) -> Score:
    """Score a set of comparisons.

    Args:
        comparisons: One per atomic requirement.
        status_scores: Score of each decided status; other statuses are excluded.

    Returns:
        The score; `compatibility_pct` is None if nothing was decided.
    """
    decided = [c for c in comparisons if c.status in status_scores]
    counts = {s: sum(1 for c in comparisons if c.status is s) for s in MatchStatus}
    total = len(comparisons) - counts[MatchStatus.NOT_APPLICABLE]
    return Score(
        compatibility_pct=(
            _PERCENT * sum(status_scores[c.status] for c in decided) / len(decided)
            if decided
            else None
        ),
        coverage_pct=_PERCENT * len(decided) / total if total else 0.0,
        decided=len(decided),
        total=total,
        needs_review=sum(1 for c in comparisons if c.needs_review),
        counts={s: n for s, n in counts.items() if n},
    )


def score_explanation(status_scores: Mapping[MatchStatus, float], min_coverage_pct: float) -> str:
    """How the numbers of a report are worked out, in plain words.

    Args:
        status_scores: Score of each decided status.
        min_coverage_pct: Coverage below this marks a manufacturer `low_coverage`.

    Returns:
        The explanation, with the scores and minimum in force.
    """
    scores = ", ".join(f"{s.value} = {v:g}" for s, v in status_scores.items())
    return (
        f"Each requirement a catalogue can decide gets a score: {scores}. "
        "Compatibility % is the total score divided by the number of decided requirements, times "
        "100; every requirement counts the same. Requirements with no evidence (unsupported) are "
        "left out of it, so they cannot lower or raise it. Coverage % is the decided requirements "
        "divided by all requirements except not_applicable, times 100, and shows how much of the "
        "tender that compatibility rests on. Manufacturers are ranked by compatibility, then "
        f"coverage; those below {min_coverage_pct:g}% coverage are marked low coverage and listed "
        "after the others, so a high score on little evidence cannot lead. General requirements "
        "(warranty, turnover, certificates) are listed separately and never enter the score."
    )


def rank(
    by_manufacturer: Mapping[str, Sequence[Comparison]],
    with_catalogue: Mapping[str, int],
    status_scores: Mapping[MatchStatus, float],
    min_coverage_pct: float,
) -> list[RankingEntry]:
    """Rank manufacturers over the whole tender.

    Args:
        by_manufacturer: Every comparison of each manufacturer, all items together.
        with_catalogue: Items for which the manufacturer had a catalogue.
        status_scores: Score of each decided status.
        min_coverage_pct: Coverage below this marks the entry `low_coverage`.

    Returns:
        Entries best first: enough coverage, compatibility (None last), coverage, then name.
    """
    scored = [(m, score(cs, status_scores)) for m, cs in by_manufacturer.items()]
    scored.sort(key=lambda ms: _order(ms[0], ms[1], min_coverage_pct))
    return [
        RankingEntry(
            rank=i,
            manufacturer=m,
            score=s,
            items_with_catalogue=with_catalogue.get(m, 0),
            low_coverage=s.coverage_pct < min_coverage_pct,
        )
        for i, (m, s) in enumerate(scored, start=1)
    ]


def _order(name: str, s: Score, min_coverage_pct: float) -> tuple[bool, bool, float, float, str]:
    """Sort key shared by the tender ranking and the per-item top vendors."""
    return (
        s.coverage_pct < min_coverage_pct,
        s.compatibility_pct is None,
        -(s.compatibility_pct or 0.0),
        -s.coverage_pct,
        name,
    )


def _source_url(report: ManufacturerItemReport) -> str | None:
    """The GeM listing page cited by the vendor's results, if any."""
    return next((e.source_url for c in report.results for e in c.evidence if e.source_url), None)


def _gem(report: ManufacturerItemReport) -> bool:
    """True if the vendor's chosen document is a GeM listing (seller-declared)."""
    return report.chosen is not None and report.chosen.seller_declared


def top_vendors(
    manufacturers: Sequence[ManufacturerItemReport], min_coverage_pct: float, limit: int
) -> list[ItemVendor]:
    """The best vendors for one item, per source: catalogues (database) and GeM listings.

    Only vendors with a document chosen for the item and at least one decision, except GeM
    vendors: a GeM listing chosen for the item is shown even when nothing could be decided
    (an item stating only its title, 8210284), after those with decisions, so an item the
    database has nothing for still shows what GeM offers. Each source is
    ranked on its own, like the tender ranking (enough coverage first, then compatibility,
    coverage, name), on the item's results alone; catalogue vendors come first in the list. A
    GeM vendor is marked as such, with the listing page.

    Args:
        manufacturers: The item's per-manufacturer reports (scores already worked out).
        min_coverage_pct: Coverage below this marks the vendor `low_coverage`.
        limit: How many to keep from each source.

    Returns:
        At most `limit` catalogue vendors, then at most `limit` GeM vendors; ranks start at 1
        in each source.
    """
    eligible = [
        m
        for m in manufacturers
        if (m.chosen or m.tied_documents) and (m.score.compatibility_pct is not None or _gem(m))
    ]
    eligible.sort(key=lambda m: _order(m.manufacturer, m.score, min_coverage_pct))
    catalogue = [m for m in eligible if not _gem(m)][:limit]
    gem_listings = [m for m in eligible if _gem(m)][:limit]
    return [
        *_vendors(catalogue, min_coverage_pct, gem=False),
        *_vendors(gem_listings, min_coverage_pct, gem=True),
    ]


def _vendors(
    ranked: Sequence[ManufacturerItemReport], min_coverage_pct: float, gem: bool
) -> list[ItemVendor]:
    """Ranked vendors of one source as `ItemVendor`s, ranks from 1."""
    out: list[ItemVendor] = []
    for i, m in enumerate(ranked, start=1):
        out.append(
            ItemVendor(
                rank=i,
                manufacturer=m.manufacturer,
                score=m.score,
                low_coverage=m.score.coverage_pct < min_coverage_pct,
                source=VendorSource.GEM_LISTING if gem else VendorSource.CATALOGUE,
                document=m.chosen.title if m.chosen else ", ".join(m.tied_documents),
                source_url=_source_url(m) if gem else None,
            )
        )
    return out
