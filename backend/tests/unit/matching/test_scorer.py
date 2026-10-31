"""Scores and ranking. Comparisons are synthetic."""

import pytest

from app.matching.scorer import rank, score
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import Comparison, LabelBasis

SCORES = {
    MatchStatus.FULL_MATCH: 1.0,
    MatchStatus.PARTIAL_MATCH: 0.5,
    MatchStatus.MISSING_CAPABILITY: 0.0,
}
FULL, PARTIAL = MatchStatus.FULL_MATCH, MatchStatus.PARTIAL_MATCH
MISSING, UNSUPPORTED = MatchStatus.MISSING_CAPABILITY, MatchStatus.UNSUPPORTED


def _c(status: MatchStatus, review: bool = False) -> Comparison:
    return Comparison(
        requirement_id="1.1",
        status=status,
        needs_review=review,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="",
    )


def test_compatibility_excludes_unsupported_and_coverage_counts_it() -> None:
    s = score([_c(FULL), _c(PARTIAL, True), _c(MISSING), _c(UNSUPPORTED, True)], SCORES)
    assert s.compatibility_pct == pytest.approx(50.0)  # (1 + 0.5 + 0) / 3
    assert s.coverage_pct == pytest.approx(75.0)  # 3 of 4 decided
    assert (s.decided, s.total, s.needs_review) == (3, 4, 2)
    assert s.counts == {FULL: 1, PARTIAL: 1, MISSING: 1, UNSUPPORTED: 1}


def test_nothing_decided_has_no_compatibility() -> None:
    s = score([_c(UNSUPPORTED, True)] * 3, SCORES)
    assert (s.compatibility_pct, s.coverage_pct) == (None, 0.0)
    assert score([], SCORES).coverage_pct == 0.0


def test_ranking_by_compatibility_then_coverage_none_last() -> None:
    ranking = rank(
        {
            "Alpha": [_c(FULL), _c(UNSUPPORTED)],  # 100 %, coverage 50
            "Beta": [_c(FULL), _c(FULL)],  # 100 %, coverage 100
            "Gamma": [_c(UNSUPPORTED)],  # nothing decided
            "Delta": [_c(MISSING), _c(FULL)],  # 50 %
        },
        {"Alpha": 1, "Beta": 1, "Delta": 1},
        SCORES,
    )
    assert [(r.rank, r.manufacturer) for r in ranking] == [
        (1, "Beta"), (2, "Alpha"), (3, "Delta"), (4, "Gamma"),
    ]  # fmt: skip
    assert ranking[-1].items_with_catalogue == 0
