"""Scores and ranking (CLAUDE.md section 7). Pure functions.

Per manufacturer: compatibility % = Σ(score * weight) / Σ(weight) over decided requirements
(full 1.0, partial 0.5, missing 0.0 by config); unsupported requirements are excluded and
show up in coverage % = decided / all atomic requirements instead. Every requirement weighs
1 for now (no weights are printed in tenders yet). Ranking: compatibility, then coverage;
a manufacturer with nothing decided ranks after every one with a score.
"""

from collections.abc import Mapping, Sequence

from app.schemas.enums import MatchStatus
from app.schemas.matching import Comparison
from app.schemas.report import RankingEntry, Score

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
    total = len(comparisons)
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


def rank(
    by_manufacturer: Mapping[str, Sequence[Comparison]],
    with_catalogue: Mapping[str, int],
    status_scores: Mapping[MatchStatus, float],
) -> list[RankingEntry]:
    """Rank manufacturers over the whole tender.

    Args:
        by_manufacturer: Every comparison of each manufacturer, all items together.
        with_catalogue: Items for which the manufacturer had a catalogue.
        status_scores: Score of each decided status.

    Returns:
        Entries best first: compatibility (None last), coverage, then name.
    """
    scored = [(m, score(cs, status_scores)) for m, cs in by_manufacturer.items()]
    scored.sort(
        key=lambda ms: (
            ms[1].compatibility_pct is None,
            -(ms[1].compatibility_pct or 0.0),
            -ms[1].coverage_pct,
            ms[0],
        )
    )
    return [
        RankingEntry(rank=i, manufacturer=m, score=s, items_with_catalogue=with_catalogue.get(m, 0))
        for i, (m, s) in enumerate(scored, start=1)
    ]
