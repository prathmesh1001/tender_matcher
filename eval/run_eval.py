"""Score match runs against every hand-labelled tender in eval/gold/.

Run from the backend folder, so its settings and paths apply:

    cd backend
    python ../eval/run_eval.py                 # rules only
    python ../eval/run_eval.py --judge         # rules, then rules + LLM judge (calls Gemini)

Writes eval/results.md, one row-by-row diff per tender in eval/diffs/, and prints the summary
table. Labels are never changed here.
"""

import argparse
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))

from app.core.config import LogLevel, get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.session import create_db_engine, create_session_factory  # noqa: E402
from app.reporting.evaluation import render_metrics, run_metrics  # noqa: E402
from app.reporting.gold_diff import gold_diff, load_gold, render_gold_diff  # noqa: E402
from app.services.judge_service import build_judge_service  # noqa: E402
from app.services.match_service import MatchService, build_match_service  # noqa: E402


def _services(judge: bool) -> dict[str, MatchService]:
    """The runs to score, by name."""
    settings = get_settings()
    services = {"rules": build_match_service(settings)}
    if judge:
        factory = create_session_factory(create_db_engine(settings))
        services["rules + judge"] = build_match_service(
            settings, judge=build_judge_service(settings, factory)
        )
    return services


def main() -> int:
    """Score every gold file and write the results."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--judge", action="store_true", help="also score rules + LLM judge")
    parser.add_argument("--out", default=str(EVAL_DIR / "results.md"))
    args = parser.parse_args()
    configure_logging(LogLevel.WARNING, json_output=False)
    services = _services(args.judge)
    golds = [load_gold(p) for p in sorted((EVAL_DIR / "gold").glob("*.yaml"))]
    diffs = [
        gold_diff(g, {name: s.match_tender(g.tender) for name, s in services.items()})
        for g in golds
    ]
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
