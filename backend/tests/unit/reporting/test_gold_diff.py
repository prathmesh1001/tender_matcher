"""Gold diff. Reports and gold here are synthetic, except the check that the real file loads."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.exceptions import NotFoundError
from app.matching.scorer import score
from app.reporting.gold_diff import gold_diff, load_gold, render_gold_diff, verdict
from app.schemas.enums import DecisionSource, MatchStatus, Operator
from app.schemas.gold import (
    GoldEvaluation,
    GoldFile,
    GoldProduct,
    GoldRequirement,
    ProductLabel,
    Verdict,
)
from app.schemas.matching import Comparison, Evidence, LabelBasis, SubTypeFit
from app.schemas.report import (
    CandidateSummary,
    ItemReport,
    ManufacturerItemReport,
    TenderReport,
    TiedDocumentRows,
)
from app.schemas.requirements import LineItem, Requirement, ValueKind

FULL, PARTIAL = MatchStatus.FULL_MATCH, MatchStatus.PARTIAL_MATCH
MISSING, UNSUPPORTED = MatchStatus.MISSING_CAPABILITY, MatchStatus.UNSUPPORTED
SCORES = {FULL: 1.0, PARTIAL: 0.5, MISSING: 0.0}
DOC = "acme.pdf"
ITEM = LineItem(item_no="1", title="T", description_lines=[], document="b.pdf", page_number=1)


def _requirement(req_id: str, value: str) -> Requirement:
    return Requirement(
        id=req_id,
        item_no="1",
        position=0,
        label="LABEL",
        kind=ValueKind.CODE,
        operator=Operator.IN,
        value_text=value,
        raw_text=value,
        document="b.pdf",
        page_number=1,
    )


def _comparison(req_id: str, status: MatchStatus, page: int | None = None) -> Comparison:
    evidence = (
        [
            Evidence(
                document=DOC,
                sha256="a" * 64,
                title="Acme",
                page_number=page,
                label="L",
                value="V",
                quote="L V",
                quote_parts=["L", "V"],
            )
        ]
        if page
        else []
    )
    return Comparison(
        requirement_id=req_id,
        status=status,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="why",
        evidence=evidence,
    )


def _candidate(document: str, fit: SubTypeFit) -> CandidateSummary:
    return CandidateSummary(
        document=document, sha256="a" * 64, title="Acme", sub_type_fit=fit, fit_score=1, decided=1
    )


def _report(results: list[Comparison], chosen: str = DOC) -> TenderReport:
    candidates = [_candidate(DOC, SubTypeFit.MATCH), _candidate("other.pdf", SubTypeFit.GENERAL)]
    manufacturer = ManufacturerItemReport(
        manufacturer="Acme",
        chosen=next(c for c in candidates if c.document == chosen),
        candidates=candidates,
        results=results,
        score=score(results, SCORES),
    )
    item = ItemReport(
        item=ITEM,
        category=None,
        product_type="valve",
        requirements=[_requirement("1.1", "WCB"), _requirement("1.2", "EXTRA")],
        manufacturers=[manufacturer],
    )
    return TenderReport(
        tender="T",
        bid_number="B",
        tender_files=[],
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ontology_used=True,
        items=[item],
        ranking=[],
    )


def _gold(status: MatchStatus = FULL) -> GoldFile:
    row = GoldRequirement(id="1.1", manual_row="Row", status=status, pages=[4], evidence="p4")
    return GoldFile(
        tender="T",
        source="test",
        evaluations=[
            GoldEvaluation(item_no="1", manufacturer="Acme", document=DOC, requirements=[row])
        ],
        products=[
            GoldProduct(item_no="1", manufacturer="Acme", document=DOC, label=ProductLabel.FULL),
            GoldProduct(
                item_no="1", manufacturer="Acme", document="other.pdf", label=ProductLabel.LEAST
            ),
        ],
    )


@pytest.mark.parametrize(
    ("gold", "status", "expected"),
    [
        (FULL, FULL, Verdict.AGREE),
        (UNSUPPORTED, UNSUPPORTED, Verdict.AGREE),
        (FULL, UNSUPPORTED, Verdict.NOT_DECIDED),
        (MISSING, UNSUPPORTED, Verdict.NOT_DECIDED),
        (FULL, PARTIAL, Verdict.WEAKER),
        (PARTIAL, MISSING, Verdict.WEAKER),
        (PARTIAL, FULL, Verdict.STRONGER),
        (UNSUPPORTED, PARTIAL, Verdict.STRONGER),
        (UNSUPPORTED, MISSING, Verdict.STRONGER),  # claims a failure gold has no evidence for
    ],
)
def test_verdict(gold: MatchStatus, status: MatchStatus, expected: Verdict) -> None:
    assert verdict(gold, status) is expected


def test_agreeing_status_on_another_page_is_flagged() -> None:
    results = [_comparison("1.1", FULL, page=11), _comparison("1.2", FULL, page=4)]
    [evaluation] = gold_diff(_gold(), {"run": _report(results)}).evaluations
    cell = evaluation.rows[0].cells["run"]
    assert (cell.verdict, cell.pages, cell.page_agrees) == (Verdict.AGREE, [11], False)


def test_unlabelled_requirements_are_listed_never_compared() -> None:
    results = [_comparison("1.1", FULL, page=4), _comparison("1.2", PARTIAL, page=4)]
    [evaluation] = gold_diff(_gold(), {"run": _report(results)}).evaluations
    assert [row.gold.id for row in evaluation.rows] == ["1.1"]
    [extra] = evaluation.unlabelled
    assert (extra.id, extra.statuses["run"]) == ("1.2", PARTIAL)


def test_run_on_another_catalogue_is_not_compared() -> None:
    results = [_comparison("1.1", FULL, page=4)]
    [evaluation] = gold_diff(_gold(), {"run": _report(results, chosen="other.pdf")}).evaluations
    assert evaluation.rows[0].cells["run"].verdict is Verdict.OTHER_CATALOGUE


def test_product_labels_compare_with_sub_type_fit() -> None:
    results = [_comparison("1.1", FULL, page=4)]
    full, least = gold_diff(_gold(), {"run": _report(results)}).products
    assert (full.fits["run"], full.chosen["run"], full.agrees["run"]) == (
        SubTypeFit.MATCH, True, True,
    )  # fmt: skip
    assert (least.fits["run"], least.chosen["run"], least.agrees["run"]) == (
        SubTypeFit.GENERAL, False, False,
    )  # fmt: skip


def test_render_shows_every_run_and_verdict() -> None:
    runs = {
        "ontology": _report([_comparison("1.1", FULL, page=4)]),
        "no ontology": _report([_comparison("1.1", UNSUPPORTED)]),
    }
    text = render_gold_diff(gold_diff(_gold(), runs))
    assert "| 1.1 | LABEL : WCB | full_match (p4) |" in text
    assert "full_match (p4): **agree**" in text
    assert "unsupported (-): **not decided**; why" in text


def test_missing_gold_file_raises(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        load_gold(tmp_path / "none.yaml")


def test_real_gold_file_is_valid() -> None:
    root = Path(__file__).resolve().parents[4]
    gold = load_gold(root / "eval" / "gold" / "8119613.yaml")
    assert gold.tender == "GEM_2026_B_8119613"
    assert len(gold.evaluations[0].requirements) == 16


# ---------------------------------------------------------------- tied catalogues


def _tied_report() -> TenderReport:
    base = _report([_comparison("1.1", PARTIAL, 3)])
    item = base.items[0]
    manufacturer = item.manufacturers[0].model_copy(
        update={
            "chosen": None,
            "tied_documents": [DOC, "other.pdf"],
            "tied_rows": [
                TiedDocumentRows(document="other.pdf", results=[_comparison("1.1", PARTIAL, 3)]),
                TiedDocumentRows(document=DOC, results=[_comparison("1.1", FULL, 7)]),
            ],
        }
    )
    return base.model_copy(
        update={"items": [item.model_copy(update={"manufacturers": [manufacturer]})]}
    )


def test_a_tied_catalogue_is_compared_by_its_own_rows() -> None:
    gold = GoldFile(
        tender="T",
        source="s",
        evaluations=[
            GoldEvaluation(
                item_no="1",
                manufacturer="Acme",
                document=DOC,
                requirements=[
                    GoldRequirement(id="1.1", manual_row="r", status=FULL, pages=[7], evidence="e")
                ],
            )
        ],
    )
    diff = gold_diff(gold, {"run": _tied_report()})
    cell = diff.evaluations[0].rows[0].cells["run"]
    assert (cell.status, cell.verdict, cell.pages) == (FULL, Verdict.AGREE, [7])
    assert "catalogues tie, none picked" in render_gold_diff(diff)
