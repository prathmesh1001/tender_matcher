"""Score match runs against every hand-labelled tender in eval/gold/.

Run from the backend folder, so its settings and paths apply:

    cd backend
    python ../eval/run_eval.py                 # rules only
    python ../eval/run_eval.py --judge         # rules, then rules + LLM judge (calls Gemini)
    python ../eval/run_eval.py --judge --judge-all   # judge every manufacturer, as the app does

With --judge, the judge sees only the item x manufacturer pairs a gold file labels: only those
are scored, so judging the others would cost Gemini calls without changing any number. The
app itself always judges every manufacturer; --judge-all does the same here.

Writes eval/results.md, one row-by-row diff per tender in eval/diffs/, and prints the summary
table. Labels are never changed here.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))

from app.core.config import LogLevel, get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.session import create_db_engine, create_session_factory  # noqa: E402
from app.matching.engine import ItemInput  # noqa: E402
from app.matching.evidence_verifier import PageTexts  # noqa: E402
from app.matching.llm_judge import ItemJudge, JudgedItems  # noqa: E402
from app.reporting.evaluation import render_metrics, run_metrics  # noqa: E402
from app.reporting.gold_diff import gold_diff, load_gold, render_gold_diff  # noqa: E402
from app.schemas.gold import GoldFile  # noqa: E402
from app.schemas.report import ItemReport  # noqa: E402
from app.services.judge_service import build_judge_service  # noqa: E402
from app.services.match_service import MatchService, build_match_service  # noqa: E402


class LabelledOnlyJudge:
    """Judges only the item x manufacturer pairs the current gold file labels."""

    def __init__(self, inner: ItemJudge) -> None:
        self._inner = inner
        self.pairs: set[tuple[str, str]] = set()

    def use(self, gold: GoldFile) -> None:
        """Judge the pairs this gold file labels from now on."""
        self.pairs = {(e.item_no, e.manufacturer) for e in gold.evaluations}

    def judge_items(
        self,
        inputs: Sequence[ItemInput],
        reports: Sequence[ItemReport],
        pages: PageTexts,
    ) -> JudgedItems:
        """Judge the labelled manufacturers; every other result is returned unchanged."""
        only = [
            r.model_copy(
                update={
                    "manufacturers": [
                        m
                        for m in r.manufacturers
                        if (r.item.item_no, m.manufacturer) in self.pairs
                    ]
                }
            )
            for r in reports
        ]
        judged = self._inner.judge_items(inputs, only, pages)
        merged: list[ItemReport] = []
        for report, done in zip(reports, judged.reports, strict=True):
            by_name = {m.manufacturer: m for m in done.manufacturers}
            manufacturers = [
                by_name.get(m.manufacturer, m) for m in report.manufacturers
            ]
            merged.append(report.model_copy(update={"manufacturers": manufacturers}))
        return JudgedItems(merged, judged.decided_by)


def _services(
    judge: bool, judge_all: bool
) -> tuple[dict[str, MatchService], LabelledOnlyJudge | None]:
    """The runs to score, by name, and the labelled-only judge to point at each gold file."""
    settings = get_settings()
    services = {"rules": build_match_service(settings)}
    labelled = None
    if judge:
        factory = create_session_factory(create_db_engine(settings))
        inner: ItemJudge = build_judge_service(settings, factory)
        if not judge_all:
            labelled = LabelledOnlyJudge(inner)
            inner = labelled
        services["rules + judge"] = build_match_service(settings, judge=inner)
    return services, labelled


def main() -> int:
    """Score every gold file and write the results."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--judge", action="store_true", help="also score rules + LLM judge"
    )
    parser.add_argument(
        "--judge-all",
        action="store_true",
        help="judge every manufacturer, not only labelled ones",
    )
    parser.add_argument("--out", default=str(EVAL_DIR / "results.md"))
    args = parser.parse_args()
    configure_logging(LogLevel.WARNING, json_output=False)
    services, labelled = _services(args.judge, args.judge_all)
    golds = [load_gold(p) for p in sorted((EVAL_DIR / "gold").glob("*.yaml"))]
    diffs = []
    for gold in golds:
        if labelled is not None:
            labelled.use(gold)
        runs = {name: s.match_tender(gold.tender) for name, s in services.items()}
        diffs.append(gold_diff(gold, runs))
    (EVAL_DIR / "diffs").mkdir(exist_ok=True)
    for diff in diffs:
        (EVAL_DIR / "diffs" / f"{diff.tender}.md").write_text(
            render_gold_diff(diff), encoding="utf-8"
        )
    metrics = [run_metrics(diffs, name) for name in services]
    text = render_metrics(metrics, [g.tender for g in golds])
    Path(args.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text.split("\n## ")[0] + f"\nWritten: {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
