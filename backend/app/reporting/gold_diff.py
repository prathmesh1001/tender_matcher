"""Compare match reports with hand-labelled gold (eval/gold/*.yaml) and render the differences.

A diff only reports: it changes no result and fills in no label. Requirements and items gold
does not label are listed as such. The system has no product label yet (Layer 2 of the manual
check), so a gold product label is compared with the catalogue's sub-type fit: LEAST means
another sub-type, FULL and PARTIAL mean the same sub-type. Metrics belong to `eval/run_eval.py`.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Final

import yaml

from app.core.exceptions import NotFoundError
from app.schemas.enums import MatchStatus
from app.schemas.gold import (
    EvaluationDiff,
    GoldDiff,
    GoldEvaluation,
    GoldFile,
    GoldProduct,
    GoldRequirement,
    ProductDiff,
    ProductLabel,
    RowDiff,
    RunCell,
    Unlabelled,
    Verdict,
)
from app.schemas.matching import Comparison, SubTypeFit
from app.schemas.report import ManufacturerItemReport, TenderReport
from app.schemas.requirements import Requirement
from app.tender.splitter import leaves

_RANK: Final = {
    MatchStatus.FULL_MATCH: 3,
    MatchStatus.PARTIAL_MATCH: 2,
    MatchStatus.MISSING_CAPABILITY: 1,
}
_EXPECTED_FIT: Final = {
    ProductLabel.FULL: SubTypeFit.MATCH,
    ProductLabel.PARTIAL: SubTypeFit.MATCH,
    ProductLabel.LEAST: SubTypeFit.MISMATCH,
}


def load_gold(path: Path) -> GoldFile:
    """Read one gold file.

    Args:
        path: A YAML file under eval/gold/.

    Returns:
        The validated gold.

    Raises:
        NotFoundError: If the file does not exist.
    """
    if not path.is_file():
        raise NotFoundError(f"No gold file {path}", details={"path": str(path)})
    return GoldFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def verdict(gold: MatchStatus, status: MatchStatus) -> Verdict:
    """How a run's status relates to the gold status."""
    if status is gold:
        return Verdict.AGREE
    if status not in _RANK:
        return Verdict.NOT_DECIDED
    if gold not in _RANK or _RANK[status] > _RANK[gold]:
        return Verdict.STRONGER
    return Verdict.WEAKER


def _manufacturer(
    report: TenderReport, item_no: str, manufacturer: str
) -> ManufacturerItemReport | None:
    """One manufacturer's results for one item, or None if the run has neither."""
    for item in report.items:
        if item.item.item_no == item_no:
            return next((m for m in item.manufacturers if m.manufacturer == manufacturer), None)
    return None


def _cell(gold: GoldRequirement, found: Comparison | None, same_catalogue: bool) -> RunCell:
    """One run's result for one gold requirement."""
    if found is None or not same_catalogue:
        return RunCell(
            status=None, pages=[], reason="", verdict=Verdict.OTHER_CATALOGUE, page_agrees=None
        )
    pages = sorted({e.page_number for e in found.evidence})
    result = verdict(gold.status, found.status)
    agrees = bool(set(pages) & set(gold.pages)) if gold.pages and result is Verdict.AGREE else None
    return RunCell(
        status=found.status, pages=pages, reason=found.reason, verdict=result, page_agrees=agrees
    )


def _text(requirement: Requirement) -> str:
    """The requirement as the tender prints it."""
    return (
        f"{requirement.label} : {requirement.value_text}"
        if requirement.label
        else (requirement.value_text)
    )


def _rows_of(found: ManufacturerItemReport | None, document: str) -> list[Comparison]:
    """A manufacturer's rows, a tied catalogue's own if gold labels that one."""
    if found is None:
        return []
    own = next((t.results for t in found.tied_rows if t.document == document), None)
    return own if own is not None else found.results


def _evaluation_diff(
    evaluation: GoldEvaluation, runs: Mapping[str, TenderReport]
) -> EvaluationDiff:
    """Compare one gold evaluation with every run."""
    reports = {
        name: _manufacturer(report, evaluation.item_no, evaluation.manufacturer)
        for name, report in runs.items()
    }
    results = {
        name: {c.requirement_id: c for c in _rows_of(m, evaluation.document)}
        for name, m in reports.items()
    }
    same = {
        name: bool(
            m
            and (
                (m.chosen and m.chosen.document == evaluation.document)
                or evaluation.document in m.tied_documents
            )
        )
        for name, m in reports.items()
    }
    atomic = _atomic(runs, evaluation.item_no)
    rows = [
        RowDiff(
            requirement=_text(atomic[gold.id]) if gold.id in atomic else "(not in the report)",
            gold=gold,
            cells={n: _cell(gold, results[n].get(gold.id), same[n]) for n in runs},
        )
        for gold in evaluation.requirements
    ]
    labelled = {g.id for g in evaluation.requirements}
    unlabelled = [
        Unlabelled(
            id=req_id,
            requirement=_text(requirement),
            statuses={n: results[n][req_id].status if req_id in results[n] else None for n in runs},
        )
        for req_id, requirement in atomic.items()
        if req_id not in labelled
    ]
    scores = {name: m.score if m else None for name, m in reports.items()}
    tied = {name: m.tied_documents if m else [] for name, m in reports.items()}
    return EvaluationDiff(
        evaluation=evaluation, rows=rows, unlabelled=unlabelled, scores=scores, tied=tied
    )


def _atomic(runs: Mapping[str, TenderReport], item_no: str) -> dict[str, Requirement]:
    """The item's atomic requirements by id, from the first run that has the item."""
    for report in runs.values():
        for item in report.items:
            if item.item.item_no == item_no:
                return {r.id: r for r in leaves(item.requirements)}
    return {}


def _product_diff(gold: GoldProduct, runs: Mapping[str, TenderReport]) -> ProductDiff:
    """Compare one gold product label with the catalogue's sub-type fit in every run."""
    fits: dict[str, SubTypeFit | None] = {}
    chosen: dict[str, bool] = {}
    for name, report in runs.items():
        found = _manufacturer(report, gold.item_no, gold.manufacturer)
        candidates = {c.document: c.sub_type_fit for c in found.candidates} if found else {}
        fits[name] = candidates.get(gold.document)
        chosen[name] = bool(found and found.chosen and found.chosen.document == gold.document)
    expected = _EXPECTED_FIT.get(gold.label)
    agrees = {name: fit is expected for name, fit in fits.items()}
    return ProductDiff(gold=gold, fits=fits, chosen=chosen, agrees=agrees)


def gold_diff(gold: GoldFile, runs: Mapping[str, TenderReport]) -> GoldDiff:
    """Compare gold with one report per run.

    Args:
        gold: The hand labels.
        runs: Reports of the same tender by run name ("ontology", "no ontology").

    Returns:
        The differences; every requirement and item without a label is listed.
    """
    labelled_items = {e.item_no for e in gold.evaluations}
    reported = [i.item.item_no for report in runs.values() for i in report.items]
    return GoldDiff(
        tender=gold.tender,
        source=gold.source,
        runs=list(runs),
        evaluations=[_evaluation_diff(e, runs) for e in gold.evaluations],
        products=[_product_diff(p, runs) for p in gold.products],
        items_without_gold=sorted(set(reported) - labelled_items),
        rankings={name: report.ranking for name, report in runs.items()},
    )


# ---------------------------------------------------------------- markdown


def _cell_text(text: str) -> str:
    """Text safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def _pages(pages: list[int]) -> str:
    return ", ".join(f"p{p}" for p in pages) or "-"


def _run_cell(cell: RunCell) -> str:
    """Status, cited pages, verdict and reason of one run, for one table cell."""
    status = cell.status.value if cell.status else "-"
    mark = cell.verdict.value
    if cell.page_agrees is False:
        mark += ", other page"
    reason = f"; {cell.reason}" if cell.reason else ""
    return _cell_text(f"{status} ({_pages(cell.pages)}): **{mark}**{reason}")


def _summary_lines(diff: GoldDiff) -> list[str]:
    """One table line per run: how many labelled rows fall under each verdict."""
    lines = ["| Run | " + " | ".join(v.value for v in Verdict) + " | other page |"]
    lines.append("|---|" + "---|" * (len(Verdict) + 1))
    for run in diff.runs:
        cells = [row.cells[run] for e in diff.evaluations for row in e.rows]
        counts = [sum(1 for c in cells if c.verdict is v) for v in Verdict]
        moved = sum(1 for c in cells if c.page_agrees is False)
        lines.append(f"| {run} | " + " | ".join(map(str, counts)) + f" | {moved} |")
    return lines


def _evaluation_lines(diff: GoldDiff, evaluation: EvaluationDiff) -> list[str]:
    """The requirement table of one evaluation, then its scores, notes and unlabelled rows."""
    gold = evaluation.evaluation
    lines = [f"## Item {gold.item_no}, {gold.manufacturer}, `{gold.document}`", ""]
    lines += ["| Req | Tender | Gold | " + " | ".join(diff.runs) + " |"]
    lines += ["|---|---|---|" + "---|" * len(diff.runs)]
    for row in evaluation.rows:
        cells = " | ".join(_run_cell(row.cells[run]) for run in diff.runs)
        label = _cell_text(f"{row.gold.status.value} ({_pages(row.gold.pages)})")
        lines.append(f"| {row.gold.id} | {_cell_text(row.requirement)} | {label} | {cells} |")
    lines += [
        "",
        f"Manual check: fit {gold.manual_fit}, coverage {gold.manual_coverage} (weighted).",
    ]
    for run in diff.runs:
        score = evaluation.scores[run]
        if score is not None:
            lines.append(
                f"System, {run}: compatibility {score.compatibility_pct}, coverage "
                f"{score.coverage_pct:.1f} % ({score.decided}/{score.total}, unweighted)."
            )
        if evaluation.tied.get(run):
            lines.append(
                f"System, {run}: catalogues tie, none picked: {', '.join(evaluation.tied[run])}."
            )
    notes = [row for row in evaluation.rows if row.gold.note]
    if notes:
        lines += ["", "Notes from gold:", ""]
        lines += [f"- {row.gold.id}: {row.gold.note}" for row in notes]
    if evaluation.unlabelled:
        lines += ["", "Not labelled in gold, so not compared:", ""]
        for extra in evaluation.unlabelled:
            shown = ", ".join(f"{n}: {s.value if s else '-'}" for n, s in extra.statuses.items())
            lines.append(f"- {extra.id} `{extra.requirement}` ({shown})")
    return [*lines, ""]


def _product_lines(diff: GoldDiff) -> list[str]:
    """Gold product labels against each run's sub-type fit."""
    lines = ["## Catalogues: product label vs sub-type fit", ""]
    lines += ["| Item | Manufacturer | Catalogue | Gold | " + " | ".join(diff.runs) + " |"]
    lines += ["|---|---|---|---|" + "---|" * len(diff.runs)]
    for product in diff.products:
        cells = []
        for run in diff.runs:
            fit = product.fits[run]
            chosen = ", chosen" if product.chosen[run] else ""
            mark = "agree" if product.agrees[run] else "**differs**"
            cells.append(f"{fit.value if fit else 'not a candidate'}{chosen}: {mark}")
        gold = product.gold
        lines.append(
            f"| {gold.item_no} | {gold.manufacturer} | `{gold.document}` | {gold.label.value} | "
            + " | ".join(cells)
            + " |"
        )
    return [*lines, ""]


def _ranking_lines(diff: GoldDiff) -> list[str]:
    """The ranking of each run; gold has no ranking, so this is shown, not compared."""
    lines = ["## Ranking (not in gold, shown only)", ""]
    for run in diff.runs:
        lines += [f"{run}:", ""]
        for entry in diff.rankings[run]:
            score = entry.score
            compatibility = (
                f"{score.compatibility_pct:.1f} %" if score.compatibility_pct is not None else "-"
            )
            low = ", low coverage" if entry.low_coverage else ""
            lines.append(
                f"{entry.rank}. {entry.manufacturer}: compatibility {compatibility}, "
                f"coverage {score.coverage_pct:.1f} %{low}"
            )
        lines.append("")
    return lines


def render_gold_diff(diff: GoldDiff) -> str:
    """The diff as markdown.

    Args:
        diff: The result of `gold_diff`.

    Returns:
        Markdown text.
    """
    lines = [f"# Gold diff: {diff.tender}", "", f"Gold source: {diff.source}", ""]
    lines += [*_summary_lines(diff), ""]
    for evaluation in diff.evaluations:
        lines += _evaluation_lines(diff, evaluation)
    if diff.items_without_gold:
        lines += [f"Items without gold: {', '.join(diff.items_without_gold)}.", ""]
    lines += _product_lines(diff) + _ranking_lines(diff)
    return "\n".join(lines).rstrip() + "\n"
