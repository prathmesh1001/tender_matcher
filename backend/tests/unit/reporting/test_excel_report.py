"""The Excel report. The report is synthetic, not real catalogue evidence."""

from datetime import UTC, datetime
from io import BytesIO

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.matching.scorer import score_explanation
from app.reporting.excel_report import excel_report
from app.reporting.status_lists import status_lists
from app.schemas.enums import (
    DecisionSource,
    ExtractionIssueCode,
    ExtractionStatus,
    MatchStatus,
    Operator,
)
from app.schemas.matching import Comparison, Evidence, LabelBasis, SubTypeFit
from app.schemas.report import (
    CandidateSummary,
    Citation,
    ExtractionIssue,
    ExtractionQuality,
    ItemReport,
    ManufacturerItemReport,
    RankingEntry,
    Score,
    SingleFirmWarning,
    TenderReport,
    TiedDocumentRows,
)
from app.schemas.requirements import LineItem, Requirement, ValueKind

SCORES = {MatchStatus.FULL_MATCH: 1.0, MatchStatus.PARTIAL_MATCH: 0.5}
SCORE = Score(
    compatibility_pct=100.0,
    coverage_pct=50.0,
    decided=1,
    total=2,
    needs_review=0,
    counts={},
)


def _req(req_id: str, text: str = "BODY : WCB") -> Requirement:
    return Requirement(
        id=req_id,
        item_no="10",
        position=0,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text="v",
        raw_text=text,
        document="spec.pdf",
        page_number=4,
    )


EVIDENCE = Evidence(
    document="alpha.pdf",
    sha256="a" * 64,
    title="Alpha",
    page_number=7,
    label="Body",
    value="A216 WCB",
    quote="Body: A216 WCB",
    quote_parts=["Body", "A216 WCB"],
    status=MatchStatus.FULL_MATCH,
    verified=True,
)


def _result(
    req_id: str = "10.1",
    status: MatchStatus = MatchStatus.FULL_MATCH,
    evidence: list[Evidence] | None = None,
    reason: str = "why",
) -> Comparison:
    return Comparison(
        requirement_id=req_id,
        status=status,
        needs_review=status is not MatchStatus.FULL_MATCH,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason=reason,
        evidence=[EVIDENCE] if evidence is None else evidence,
    )


def _candidate(document: str = "alpha.pdf") -> CandidateSummary:
    return CandidateSummary(
        document=document,
        sha256="a" * 64,
        title=document,
        sub_type_fit=SubTypeFit.UNKNOWN,
        fit_score=1.0,
        decided=1,
    )


def _report(**fields: object) -> TenderReport:
    item = ItemReport(
        item=LineItem(
            item_no="10", title="VALVE", description_lines=[], document="spec.pdf", page_number=1
        ),
        category=None,
        product_type="ball_valve",
        requirements=[_req("10.1"), _req("10.2", "=SUM(A1:A9) evil\x07 line")],
        manufacturers=[
            ManufacturerItemReport(
                manufacturer="Acme",
                chosen=_candidate(),
                candidates=[],
                results=[
                    _result(),
                    _result("10.2", MatchStatus.UNSUPPORTED, evidence=[], reason="no field"),
                ],
                score=SCORE,
            )
        ],
    )
    values: dict[str, object] = {
        "tender": "GEM_2099_B_1",
        "bid_number": "GEM/2099/B/1",
        "tender_revision": 2,
        "bid_sha256": "b" * 64,
        "tender_files": ["bid.pdf", "spec.pdf"],
        "generated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "ontology_used": True,
        "items": [item],
        "ranking": [
            RankingEntry(
                rank=1, manufacturer="Acme", score=SCORE, items_with_catalogue=1, low_coverage=True
            )
        ],
        "status_scores": SCORES,
        "score_explanation": score_explanation(SCORES, 50.0),
        "status_lists": status_lists([item]),
        "general_requirements": [
            _req("g.1", "Warranty 12 months").model_copy(
                update={"category": "warranty", "mandatory": True}
            )
        ],
        "general_results": [_result("g.1", MatchStatus.UNSUPPORTED, evidence=[], reason="vendor")],
    }
    return TenderReport.model_validate({**values, **fields})


def _sheet(report: TenderReport, name: str) -> Worksheet:
    sheet = load_workbook(BytesIO(excel_report(report)))[name]
    assert isinstance(sheet, Worksheet)
    return sheet


def _rows(sheet: Worksheet) -> list[list[object]]:
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def _flat(sheet: Worksheet) -> str:
    return " | ".join(str(v) for row in _rows(sheet) for v in row if v is not None)


def test_the_workbook_has_the_four_sheets() -> None:
    book = load_workbook(BytesIO(excel_report(_report())))
    assert book.sheetnames == ["Summary", "Requirements", "By status", "General"]


def test_summary_shows_revision_ranking_and_the_formula_in_words() -> None:
    text = _flat(_sheet(_report(), "Summary"))
    assert "Tender GEM/2099/B/1" in text
    assert "Revision of bid.pdf | 2" in text
    assert "b" * 64 in text
    assert "1 | Acme | 100 | 50 | 1 | 2 | low coverage" in text
    assert "full_match = 1, partial_match = 0.5" in text
    assert "below 50% coverage" in text
    assert "Warnings" not in text


def test_summary_puts_warnings_before_the_ranking() -> None:
    quality = ExtractionQuality(
        status=ExtractionStatus.UNRELIABLE,
        reasons=[ExtractionIssue(code=ExtractionIssueCode.OVERSIZED_ITEM, reason="item 1 is big")],
    )
    firm = SingleFirmWarning(
        makes=["Acme"], citations=[Citation(document="bid.pdf", page_number=3, quote="only Acme")]
    )
    text = _flat(_sheet(_report(extraction_quality=quality, single_firm=firm), "Summary"))
    assert "every result below needs review" in text
    assert "oversized_item: item 1 is big" in text
    assert "only one firm is acceptable: Acme" in text
    assert "bid.pdf p3: only Acme" in text
    assert text.index("item 1 is big") < text.index("Ranking")


def test_a_requirement_row_has_every_column_of_the_required_table() -> None:
    header, decided, undecided = _rows(_sheet(_report(), "Requirements"))
    assert header[:11] == [
        "Item",
        "Requirement",
        "Manufacturer",
        "Capability",
        "Status",
        "Score",
        "Document",
        "Page",
        "Quote",
        "Confidence",
        "Needs review",
    ]
    assert decided[:11] == [
        "10",
        "BODY : WCB",
        "Acme",
        "Body: A216 WCB",
        "full_match",
        1,
        "alpha.pdf",
        "7",
        "Body: A216 WCB",
        None,  # not computed for this synthetic row
        "no",
    ]
    assert undecided[4:6] == ["unsupported", None]  # excluded from the %, not a zero
    assert undecided[9:11] == [None, "yes"]


def test_confidence_shows_its_value_and_what_lowered_it() -> None:
    report = _report()
    item = report.items[0]
    judged = _result("10.1", MatchStatus.FULL_MATCH, evidence=[EVIDENCE]).model_copy(
        update={"confidence": 0.7, "confidence_factors": ["LLM decision x0.7"]}
    )
    m = item.manufacturers[0].model_copy(update={"results": [judged]})
    report = report.model_copy(update={"items": [item.model_copy(update={"manufacturers": [m]})]})
    _, row = _rows(_sheet(report, "Requirements"))
    assert row[9] == "0.70 (LLM decision x0.7)"


def test_a_quote_on_an_undecided_row_is_marked_as_not_evidence() -> None:
    report = _report()
    item = report.items[0]
    cited = _result("10.1", MatchStatus.UNSUPPORTED, evidence=[EVIDENCE])
    m = item.manufacturers[0].model_copy(update={"results": [cited]})
    changed = report.model_copy(update={"items": [item.model_copy(update={"manufacturers": [m]})]})
    _, row = _rows(_sheet(changed, "Requirements"))
    assert row[8] == "No evidence. Cited for review only, this decides nothing:\nBody: A216 WCB"


def test_a_page_cited_twice_is_listed_once() -> None:
    report = _report()
    item = report.items[0]
    twice = _result(evidence=[EVIDENCE, EVIDENCE.model_copy(update={"page_number": 3}), EVIDENCE])
    m = item.manufacturers[0].model_copy(update={"results": [twice]})
    changed = report.model_copy(update={"items": [item.model_copy(update={"manufacturers": [m]})]})
    _, row = _rows(_sheet(changed, "Requirements"))
    assert row[7] == "3, 7"


def test_pdf_text_stays_text_and_control_characters_are_dropped() -> None:
    sheet = _sheet(_report(), "Requirements")
    cell = sheet["B3"]
    assert cell.value == "=SUM(A1:A9) evil line"
    assert cell.data_type == "s"


def test_every_tied_catalogue_gets_its_own_rows_and_none_looks_picked() -> None:
    report = _report()
    item = report.items[0]
    tied = item.manufacturers[0].model_copy(
        update={
            "chosen": None,
            "results": [_result()],
            "tied_documents": ["alpha.pdf", "beta.pdf"],
            "tied_rows": [
                TiedDocumentRows(document="alpha.pdf", results=[_result()]),
                TiedDocumentRows(
                    document="beta.pdf", results=[_result(status=MatchStatus.PARTIAL_MATCH)]
                ),
            ],
        }
    )
    changed = report.model_copy(
        update={"items": [item.model_copy(update={"manufacturers": [tied]})]}
    )
    _, first, second = _rows(_sheet(changed, "Requirements"))
    assert (first[6], first[4]) == ("alpha.pdf (tied)", "full_match")
    assert (second[6], second[4], second[5]) == ("beta.pdf (tied)", "partial_match", 0.5)


def test_by_status_lists_requirements_per_manufacturer() -> None:
    _, full, unsupported = _rows(_sheet(_report(), "By status"))
    assert full == ["Acme", "full_match", "10", "BODY : WCB", "no"]
    assert unsupported[:3] == ["Acme", "unsupported", "10"]


def test_general_requirements_are_on_their_own_sheet() -> None:
    _, row = _rows(_sheet(_report(), "General"))
    assert row == [
        "warranty",
        "Warranty 12 months",
        "unsupported",
        "spec.pdf",
        4,
        "yes",
        "yes",
        "vendor",
    ]


def test_capability_is_the_field_that_decided_nothing_otherwise() -> None:
    assert _result().capability == "Body: A216 WCB"
    undeciding = EVIDENCE.model_copy(update={"status": None})
    assert _result(status=MatchStatus.UNSUPPORTED, evidence=[undeciding]).capability is None
    assert _result(status=MatchStatus.UNSUPPORTED, evidence=[]).capability is None


def test_status_lists_group_and_keep_status_order() -> None:
    [block] = _report().status_lists
    assert block.manufacturer == "Acme"
    assert list(block.lists) == [MatchStatus.FULL_MATCH, MatchStatus.UNSUPPORTED]
    assert block.lists[MatchStatus.FULL_MATCH][0].requirement == "BODY : WCB"
