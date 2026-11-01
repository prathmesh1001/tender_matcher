"""ReportService against Postgres. The report is synthetic, not real catalogue evidence."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidInputError, NotFoundError
from app.db.models import Document, Run
from app.db.repositories import (
    DocumentRepository,
    ResultRepository,
    RunRepository,
    TenderRepository,
)
from app.schemas.enums import DecisionSource, MatchStatus, Operator
from app.schemas.matching import Comparison, Evidence, LabelBasis
from app.schemas.report import (
    ItemReport,
    ManufacturerItemReport,
    Score,
    TenderReport,
)
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.services.report_service import ReportService
from tests.integration.factories import make_document, make_run, make_tender

SCORES = {MatchStatus.FULL_MATCH: 1.0}
SCORE = Score(compatibility_pct=None, coverage_pct=0, decided=0, total=0, needs_review=0, counts={})


def _req(req_id: str, parent_id: str | None = None) -> Requirement:
    return Requirement(
        id=req_id,
        parent_id=parent_id,
        item_no="1",
        position=0,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text="v",
        raw_text="printed “line”",
        document="bid.pdf",
        page_number=3,
    )


def _report(sha256: str) -> TenderReport:
    evidence = Evidence(
        document="alpha.pdf",
        sha256=sha256,
        title="Alpha",
        page_number=2,
        label="Body",
        value="WCB",
        quote="Body: WCB",
        quote_parts=["Body", "WCB"],
        verified=True,
        match_score=1.0,
    )
    result = Comparison(
        requirement_id="1.1.1",
        status=MatchStatus.FULL_MATCH,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="why",
        evidence=[evidence],
    )
    general = result.model_copy(
        update={"requirement_id": "g.1", "status": MatchStatus.NOT_APPLICABLE, "evidence": []}
    )
    item = ItemReport(
        item=LineItem(
            item_no="1", title="VALVE", description_lines=[], document="bid.pdf", page_number=1
        ),
        category=None,
        product_type=None,
        requirements=[_req("1.1"), _req("1.1.1", parent_id="1.1")],
        manufacturers=[
            ManufacturerItemReport(
                manufacturer="Acme", chosen=None, candidates=[], results=[result], score=SCORE
            )
        ],
    )
    return TenderReport(
        tender="T",
        bid_number="B",
        tender_files=["bid.pdf"],
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ontology_used=True,
        items=[item],
        ranking=[],
        general_requirements=[_req("g.1")],
        general_results=[general],
    )


@pytest.fixture
def run(db_session: Session) -> Run:
    tender = TenderRepository(db_session).add(make_tender())
    return RunRepository(db_session).add(make_run(tender))


@pytest.fixture
def document(db_session: Session) -> Document:
    return DocumentRepository(db_session).add(make_document())


@pytest.fixture
def service(db_session: Session) -> ReportService:
    return ReportService(
        RunRepository(db_session),
        ResultRepository(db_session),
        DocumentRepository(db_session),
        SCORES,
    )


def test_save_stores_rows_evidence_and_the_report(
    db_session: Session, service: ReportService, run: Run, document: Document
) -> None:
    report = _report(document.sha256)
    counts = service.save(run.id, report)
    assert (counts.line_items, counts.requirements, counts.results) == (1, 3, 2)
    db_session.expire_all()

    runs = RunRepository(db_session)
    [item] = runs.list_line_items(run.id)
    parent, child, general = runs.list_requirements(run.id)
    assert child.parent_id == parent.id
    assert child.line_item_id == item.id
    assert general.line_item_id is None
    assert parent.raw_text == "printed “line”"

    results = {r.requirement_id: r for r in ResultRepository(db_session).list_for_run(run.id)}
    [evidence] = results[child.id].evidence
    assert evidence.document_id == document.id
    assert (evidence.page_number, evidence.quote) == (2, "Body: WCB")
    assert results[general.id].manufacturer is None
    assert results[general.id].score is None

    assert run.report is not None
    assert TenderReport.model_validate(run.report) == report


def test_a_run_saves_its_report_only_once(
    service: ReportService, run: Run, document: Document
) -> None:
    service.save(run.id, _report(document.sha256))
    with pytest.raises(InvalidInputError):
        service.save(run.id, _report(document.sha256))


def test_evidence_from_an_unstored_catalogue_is_refused_and_nothing_is_written(
    db_session: Session, service: ReportService, run: Run
) -> None:
    with pytest.raises(NotFoundError) as caught:
        service.save(run.id, _report("f" * 64))
    assert caught.value.details == {"sha256": ["f" * 64]}
    assert RunRepository(db_session).list_requirements(run.id) == []
    assert run.report is None


def test_an_unknown_run_is_not_found(service: ReportService, document: Document) -> None:
    with pytest.raises(NotFoundError):
        service.save(uuid.uuid7(), _report(document.sha256))
