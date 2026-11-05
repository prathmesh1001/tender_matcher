"""Accuracy metrics of match runs against hand labels (eval/gold/*.yaml).

Built on `gold_diff`, so a row is classified the same way in the diff and in the metrics. Rows
where the run chose another catalogue than the label are counted apart: their statuses describe
different documents and are not comparable.

- Confusion matrix: label status x run status, over comparable rows.
- Per status: precision (of the rows the run gave that status, how many the label agrees with)
  and recall (of the rows labelled with it, how many the run found).
- Decided accuracy: of the rows the run decided, how many agree. Coverage: decided / comparable.
- Stronger: decisions claiming more than the label, the direction that can mislead a buyer.
- Citation accuracy: of agreeing decisions whose label cites pages, how many cite one of them.
- Confidence: how often decisions are wrong at full confidence and below it.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from app.schemas.enums import MatchStatus
from app.schemas.gold import GoldDiff, RunCell, Verdict

STATUSES: Final = (
    MatchStatus.FULL_MATCH,
    MatchStatus.PARTIAL_MATCH,
    MatchStatus.MISSING_CAPABILITY,
    MatchStatus.UNSUPPORTED,
)
_UNDECIDED: Final = frozenset({MatchStatus.UNSUPPORTED, MatchStatus.NOT_APPLICABLE})


class StatusMetrics(BaseModel):
    """Precision and recall of one status; None when there is nothing to divide by."""

    model_config = ConfigDict(frozen=True)

    status: MatchStatus
    labelled: int
    predicted: int
    correct: int
    precision: float | None
    recall: float | None


class Ratio(BaseModel):
    """A count out of a total; `value` is None when the total is zero."""

    model_config = ConfigDict(frozen=True)

    count: int
    total: int

    @property
    def value(self) -> float | None:
        """The share, or None for an empty total."""
        return self.count / self.total if self.total else None


class RunMetrics(BaseModel):
    """Every metric of one run over a set of labelled rows."""

    model_config = ConfigDict(frozen=True)

    run: str
    labelled: int
    other_catalogue: int
    confusion: dict[str, dict[str, int]]
    per_status: list[StatusMetrics]
    decided_accuracy: Ratio
    coverage: Ratio
    stronger: int
    citation_accuracy: Ratio
    wrong_at_full_confidence: Ratio
    wrong_below_full_confidence: Ratio


def run_cells(diffs: Iterable[GoldDiff], run: str) -> list[tuple[MatchStatus, RunCell]]:
    """Every labelled row of one run: the label's status and the run's cell.

    Args:
        diffs: Gold diffs, one per tender.
        run: The run's name in the diffs.

    Returns:
        `(label status, cell)` pairs, in file order.
    """
    return [
        (row.gold.status, row.cells[run])
        for diff in diffs
        for evaluation in diff.evaluations
        for row in evaluation.rows
        if run in row.cells
    ]


def _status_metrics(
    pairs: Sequence[tuple[MatchStatus, MatchStatus]],
) -> list[StatusMetrics]:
    """Precision and recall of each status over `(label, run)` pairs."""
    out: list[StatusMetrics] = []
    for status in STATUSES:
        labelled = sum(gold is status for gold, _ in pairs)
        predicted = sum(found is status for _, found in pairs)
        correct = sum(gold is status and found is status for gold, found in pairs)
        out.append(
            StatusMetrics(
                status=status,
                labelled=labelled,
                predicted=predicted,
                correct=correct,
                precision=correct / predicted if predicted else None,
                recall=correct / labelled if labelled else None,
            )
        )
    return out


def _confusion(
    pairs: Sequence[tuple[MatchStatus, MatchStatus]],
) -> dict[str, dict[str, int]]:
    """Label status -> run status -> rows."""
    counts = Counter((gold.value, found.value) for gold, found in pairs)
    return {g.value: {f.value: counts[(g.value, f.value)] for f in STATUSES} for g in STATUSES}


def _confidence_errors(cells: Sequence[tuple[MatchStatus, RunCell]], full: bool) -> Ratio:
    """Wrong decisions among those at full confidence, or among those below it."""
    decided = [
        (gold, cell)
        for gold, cell in cells
        if cell.status is not None
        and cell.status not in _UNDECIDED
        and cell.confidence is not None
        and (cell.confidence >= 1.0) is full
    ]
    return Ratio(count=sum(c.status is not g for g, c in decided), total=len(decided))


def run_metrics(diffs: Sequence[GoldDiff], run: str) -> RunMetrics:
    """Every metric of one run over all labelled rows.

    Args:
        diffs: Gold diffs, one per tender.
        run: The run's name in the diffs.

    Returns:
        The metrics.
    """
    cells = run_cells(diffs, run)
    comparable = [(g, c) for g, c in cells if c.verdict is not Verdict.OTHER_CATALOGUE]
    pairs = [(g, c.status) for g, c in comparable if c.status is not None]
    decided = [(g, s) for g, s in pairs if s not in _UNDECIDED]
    cited = [c for _, c in comparable if c.page_agrees is not None]
    return RunMetrics(
        run=run,
        labelled=len(cells),
        other_catalogue=len(cells) - len(comparable),
        confusion=_confusion(pairs),
        per_status=_status_metrics(pairs),
        decided_accuracy=Ratio(count=sum(g is s for g, s in decided), total=len(decided)),
        coverage=Ratio(count=len(decided), total=len(comparable)),
        stronger=sum(c.verdict is Verdict.STRONGER for _, c in comparable),
        citation_accuracy=Ratio(count=sum(bool(c.page_agrees) for c in cited), total=len(cited)),
        wrong_at_full_confidence=_confidence_errors(comparable, full=True),
        wrong_below_full_confidence=_confidence_errors(comparable, full=False),
    )


def _pct(ratio: Ratio) -> str:
    """`75% (3/4)`, or a dash for an empty total."""
    value = ratio.value
    return "-" if value is None else f"{value:.0%} ({ratio.count}/{ratio.total})"


def _share(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def render_metrics(metrics: Sequence[RunMetrics], tenders: Sequence[str]) -> str:
    """Markdown with a summary table, per-status metrics and a confusion matrix per run.

    Args:
        metrics: One entry per run.
        tenders: Tenders the labels come from.

    Returns:
        The document.
    """
    lines = [
        "# Evaluation against hand labels",
        "",
        f"Tenders: {', '.join(tenders)}. Generated by `eval/run_eval.py`; do not edit by hand.",
        "",
        "| Run | Labelled | Other catalogue | Coverage | Decided accuracy | Stronger | Citations |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in metrics:
        lines.append(
            f"| {m.run} | {m.labelled} | {m.other_catalogue} | {_pct(m.coverage)} | "
            f"{_pct(m.decided_accuracy)} | {m.stronger} | {_pct(m.citation_accuracy)} |"
        )
    for m in metrics:
        lines += _run_lines(m)
    return "\n".join(lines) + "\n"


def _run_lines(m: RunMetrics) -> list[str]:
    """Per-status metrics, confusion matrix and confidence check of one run."""
    lines = [
        "",
        f"## {m.run}",
        "",
        "| Status | Labelled | Run gave | Correct | Precision | Recall |",
        "|---|---|---|---|---|---|",
    ]
    for s in m.per_status:
        lines.append(
            f"| {s.status.value} | {s.labelled} | {s.predicted} | {s.correct} | "
            f"{_share(s.precision)} | {_share(s.recall)} |"
        )
    lines += [
        "",
        "Confusion matrix (rows: label, columns: run):",
        "",
        "| Label \\ run | " + " | ".join(s.value for s in STATUSES) + " |",
        "|---|" + "---|" * len(STATUSES),
    ]
    for gold, row in m.confusion.items():
        lines.append(f"| {gold} | " + " | ".join(str(row[s.value]) for s in STATUSES) + " |")
    lines += [
        "",
        f"Wrong decisions at full confidence: {_pct(m.wrong_at_full_confidence)}. "
        f"Below full confidence: {_pct(m.wrong_below_full_confidence)}.",
    ]
    return lines
