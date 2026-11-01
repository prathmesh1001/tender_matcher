"""A report as database rows. The report is synthetic; nothing touches a database."""

import uuid
from datetime import UTC, datetime

import pytest

from app.reporting.report_rows import RowContext, cited_hashes, report_rows
from app.schemas.enums import DecisionSource, MatchStatus, Operator
from app.schemas.matching import Comparison, Evidence, LabelBasis, SubTypeFit
from app.schemas.report import (
    CandidateSummary,
    ItemReport,
    ManufacturerItemReport,
    Score,
    TenderReport,
    TiedDocumentRows,
)
from app.schemas.requirements import LineItem, Requirement, ValueKind

SHA = "a" * 64
DOC_ID = uuid.uuid7()
SCORES = {MatchStatus.FULL_MATCH: 1.0, MatchStatus.MISSING_CAPABILITY: 0.0}
CTX = RowContext(uuid.uuid7(), {SHA: DOC_ID}, SCORES)
SCORE = Score(compatibility_pct=None, coverage_pct=0, decided=0, total=0, needs_review=0, counts={})


def _req(req_id: str, parent_id: str | None = None, item_no: str = "1") -> Requirement:
    return Requirement(
        id=req_id,
        parent_id=parent_id,
        item_no=item_no,
        position=0,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text="v",
        raw_text="printed line",
        document="bid.pdf",
        page_number=3,
    )


def _evidence() -> Evidence:
    return Evidence(
        document="alpha.pdf",
        sha256=SHA,
        title="Alpha",
        page_number=2,
        label="Body",
        value="WCB",
        quote="Body: WCB",
        quote_parts=["Body", "WCB"],
        verified=True,
        match_score=0.97,
    )


def _result(
    req_id: str,
    status: MatchStatus = MatchStatus.FULL_MATCH,
    evidence: list[Evidence] | None = None,
) -> Comparison:
    return Comparison(
        requirement_id=req_id,
        status=status,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="why",
        evidence=evidence or [],
    )


def _candidate(document: str) -> CandidateSummary:
    return CandidateSummary(
        document=document,
        sha256=SHA,
        title=document,
        sub_type_fit=SubTypeFit.UNKNOWN,
        product_heading="Model X",
        fit_score=1.0,
        decided=1,
    )


def _manufacturer(results: list[Comparison], **fields: object) -> ManufacturerItemReport:
    values: dict[str, object] = {
        "manufacturer": "Acme",
        "chosen": _candidate("alpha.pdf"),
        "candidates": [],
        "results": results,
        "score": SCORE,
    }
    return ManufacturerItemReport.model_validate({**values, **fields})


def _item(
    requirements: list[Requirement],
    manufacturers: list[ManufacturerItemReport],
    item_no: str = "1",
) -> ItemReport:
    return ItemReport(
        item=LineItem(
            item_no=item_no,
            title="VALVE",
            description_lines=["a"],
            quantity="2",
            unit="Nos",
            document="bid.pdf",
            page_number=1,
        ),
        category="valves_pumps",
        product_type="ball_valve",
        requirements=requirements,
        manufacturers=manufacturers,
    )


def _report(items: list[ItemReport], **fields: object) -> TenderReport:
    values: dict[str, object] = {
        "tender": "T",
        "bid_number": "B",
        "tender_files": [],
        "generated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "ontology_used": True,
        "items": items,
        "ranking": [],
    }
    return TenderReport.model_validate({**values, **fields})


def test_parent_links_and_positions_run_across_the_whole_report() -> None:
    first = _item([_req("1.1"), _req("1.1.1", parent_id="1.1")], [])
    second = _item([_req("2.1", item_no="2")], [], item_no="2")
    rows = report_rows(_report([first, second], general_requirements=[_req("g.1")]), CTX)

    assert [r.ref for r in rows.requirements] == ["1.1", "1.1.1", "2.1", "g.1"]
    assert [r.position for r in rows.requirements] == [0, 1, 2, 3]
    assert rows.requirements[1].parent_id == rows.requirements[0].id
    assert rows.requirements[0].line_item_id == rows.line_items[0].id
    assert rows.requirements[2].line_item_id == rows.line_items[1].id
    assert rows.requirements[3].line_item_id is None
    assert [i.position for i in rows.line_items] == [0, 1]


def test_results_link_through_their_own_item_when_numbers_repeat() -> None:
    first = _item([_req("1.1")], [_manufacturer([_result("1.1")])])
    second = _item([_req("1.1")], [_manufacturer([_result("1.1")])])
    rows = report_rows(_report([first, second]), CTX)

    assert [i.item_no for i in rows.line_items] == ["1", "1"]
    assert [r.requirement_id for r in rows.results] == [r.id for r in rows.requirements]
    assert rows.requirements[0].id != rows.requirements[1].id


def test_a_result_keeps_its_decision_score_and_chosen_catalogue() -> None:
    results = [_result("1.1"), _result("1.2", MatchStatus.MISSING_CAPABILITY)]
    rows = report_rows(
        _report([_item([_req("1.1"), _req("1.2")], [_manufacturer(results)])]), CTX
    ).results

    assert (rows[0].manufacturer, rows[0].catalogue) == ("Acme", "alpha.pdf")
    assert rows[0].product_family == "Model X"
    assert rows[0].reasoning == "why"
    assert (rows[0].score, rows[1].score) == (1.0, 0.0)
    assert rows[0].tied is False


def test_an_undecided_result_has_no_score_rather_than_zero() -> None:
    [row] = report_rows(
        _report([_item([_req("1.1")], [_manufacturer([_result("1.1", MatchStatus.UNSUPPORTED)])])]),
        CTX,
    ).results
    assert row.score is None


def test_evidence_keeps_page_quote_and_its_stored_document() -> None:
    result = _result("1.1", evidence=[_evidence()])
    [row] = report_rows(_report([_item([_req("1.1")], [_manufacturer([result])])]), CTX).results
    [evidence] = row.evidence
    assert evidence.document_id == DOC_ID
    assert (evidence.page_number, evidence.quote) == (2, "Body: WCB")
    assert evidence.quote_parts == ["Body", "WCB"]
    assert (evidence.verified, evidence.match_score) == (True, 0.97)


def test_no_catalogue_gives_a_row_with_no_catalogue() -> None:
    manufacturer = _manufacturer([_result("1.1", MatchStatus.UNSUPPORTED)], chosen=None)
    [row] = report_rows(_report([_item([_req("1.1")], [manufacturer])]), CTX).results
    assert (row.manufacturer, row.catalogue, row.product_family) == ("Acme", None, None)


def test_tied_catalogues_give_one_row_each_and_no_picked_catalogue() -> None:
    first = [_result("1.1", evidence=[_evidence()])]
    second = [_result("1.1", MatchStatus.MISSING_CAPABILITY)]
    manufacturer = _manufacturer(
        first,  # `results` repeats the first tied catalogue
        chosen=None,
        tied_documents=["alpha.pdf", "beta.pdf"],
        tied_rows=[
            TiedDocumentRows(document="alpha.pdf", results=first),
            TiedDocumentRows(document="beta.pdf", results=second),
        ],
    )
    rows = report_rows(_report([_item([_req("1.1")], [manufacturer])]), CTX).results

    assert [(r.catalogue, r.tied, r.status) for r in rows] == [
        ("alpha.pdf", True, MatchStatus.FULL_MATCH),
        ("beta.pdf", True, MatchStatus.MISSING_CAPABILITY),
    ]


def test_a_general_result_has_no_manufacturer() -> None:
    report = _report(
        [],
        general_requirements=[_req("g.1")],
        general_results=[_result("g.1", MatchStatus.NOT_APPLICABLE)],
    )
    [row] = report_rows(report, CTX).results
    assert (row.manufacturer, row.catalogue, row.tied) == (None, None, False)


def test_cited_hashes_cover_tied_rows_too() -> None:
    other = _evidence().model_copy(update={"sha256": "b" * 64})
    tied = [_result("1.1", evidence=[other])]
    manufacturer = _manufacturer(
        [_result("1.1", evidence=[_evidence()])],
        tied_rows=[TiedDocumentRows(document="beta.pdf", results=tied)],
    )
    assert cited_hashes(_report([_item([_req("1.1")], [manufacturer])])) == {SHA, "b" * 64}


def test_a_catalogue_missing_from_the_context_is_an_error() -> None:
    other = _evidence().model_copy(update={"sha256": "b" * 64})
    report = _report([_item([_req("1.1")], [_manufacturer([_result("1.1", evidence=[other])])])])
    with pytest.raises(KeyError):
        report_rows(report, CTX)
