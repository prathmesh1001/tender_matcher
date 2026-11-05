"""Accuracy metrics over gold diffs. Labels and run cells are synthetic."""

from app.reporting.evaluation import render_metrics, run_metrics
from app.reporting.gold_diff import verdict
from app.schemas.enums import MatchStatus
from app.schemas.gold import (
    EvaluationDiff,
    GoldDiff,
    GoldEvaluation,
    GoldRequirement,
    RowDiff,
    RunCell,
    Verdict,
)

F, P, M, U = (
    MatchStatus.FULL_MATCH,
    MatchStatus.PARTIAL_MATCH,
    MatchStatus.MISSING_CAPABILITY,
    MatchStatus.UNSUPPORTED,
)


def _row(
    i: int,
    gold: MatchStatus,
    found: MatchStatus | None,
    pages: list[int] | None = None,
    gold_pages: list[int] | None = None,
    confidence: float | None = None,
) -> RowDiff:
    label = GoldRequirement(
        id=f"1.{i}", manual_row="r", status=gold, pages=gold_pages or [], evidence="e"
    )
    if found is None:
        cell = RunCell(
            status=None,
            pages=[],
            reason="",
            verdict=Verdict.OTHER_CATALOGUE,
            page_agrees=None,
        )
    else:
        result = verdict(gold, found)
        cited = pages or []
        agrees = (
            bool(set(cited) & set(gold_pages)) if gold_pages and result is Verdict.AGREE else None
        )
        cell = RunCell(
            status=found,
            pages=cited,
            reason="",
            verdict=result,
            page_agrees=agrees,
            confidence=confidence,
        )
    return RowDiff(requirement="x", gold=label, cells={"rules": cell})


def _diff(rows: list[RowDiff]) -> GoldDiff:
    evaluation = GoldEvaluation(
        item_no="1",
        manufacturer="Acme",
        document="a.pdf",
        requirements=[r.gold for r in rows],
    )
    return GoldDiff(
        tender="T",
        source="synthetic",
        runs=["rules"],
        evaluations=[EvaluationDiff(evaluation=evaluation, rows=rows, unlabelled=[], scores={})],
        products=[],
        items_without_gold=[],
        rankings={},
    )


ROWS = [
    _row(1, F, F, pages=[4], gold_pages=[4], confidence=1.0),  # agree, right page
    _row(2, F, F, pages=[9], gold_pages=[4], confidence=1.0),  # agree, other page
    _row(3, F, P, confidence=0.8),  # weaker
    _row(4, M, F, confidence=0.8),  # stronger
    _row(5, F, U),  # not decided
    _row(6, U, U),  # agree undecided
    _row(7, F, None),  # other catalogue
]


def test_other_catalogue_rows_are_counted_apart() -> None:
    m = run_metrics([_diff(ROWS)], "rules")
    assert (m.labelled, m.other_catalogue) == (7, 1)


def test_coverage_and_accuracy_count_decided_rows_only() -> None:
    m = run_metrics([_diff(ROWS)], "rules")
    assert (m.coverage.count, m.coverage.total) == (4, 6)
    assert (m.decided_accuracy.count, m.decided_accuracy.total) == (2, 4)
    assert m.stronger == 1


def test_precision_and_recall_per_status() -> None:
    by_status = {s.status: s for s in run_metrics([_diff(ROWS)], "rules").per_status}
    full = by_status[F]
    assert (full.labelled, full.predicted, full.correct) == (4, 3, 2)
    assert full.precision == 2 / 3
    assert full.recall == 0.5
    assert by_status[U].precision == 0.5  # one of two unsupported rows was labelled unsupported
    assert by_status[M].precision is None  # never predicted: nothing to divide by


def test_confusion_matrix_puts_the_label_in_rows() -> None:
    m = run_metrics([_diff(ROWS)], "rules")
    assert m.confusion["full_match"] == {
        "full_match": 2,
        "partial_match": 1,
        "missing_capability": 0,
        "unsupported": 1,
    }
    assert m.confusion["missing_capability"]["full_match"] == 1


def test_citation_accuracy_counts_agreeing_rows_with_labelled_pages() -> None:
    m = run_metrics([_diff(ROWS)], "rules")
    assert (m.citation_accuracy.count, m.citation_accuracy.total) == (1, 2)


def test_confidence_check_splits_decisions_at_full_confidence() -> None:
    m = run_metrics([_diff(ROWS)], "rules")
    assert (m.wrong_at_full_confidence.count, m.wrong_at_full_confidence.total) == (
        0,
        2,
    )
    assert (
        m.wrong_below_full_confidence.count,
        m.wrong_below_full_confidence.total,
    ) == (2, 2)


def test_empty_totals_render_as_a_dash() -> None:
    m = run_metrics([_diff([_row(1, F, None)])], "rules")
    assert m.decided_accuracy.value is None
    text = render_metrics([m], ["T"])
    assert "| rules | 1 | 1 | - | - | 0 | - |" in text
    assert "Confusion matrix" in text
