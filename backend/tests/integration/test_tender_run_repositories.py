"""TenderRepository, RunRepository and ResultRepository against Postgres."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Run
from app.db.repositories import (
    DocumentRepository,
    ResultRepository,
    RunRepository,
    TenderRepository,
)
from app.schemas.enums import Operator, RunStatus
from app.schemas.requirements import ValueKind
from tests.integration.factories import (
    make_document,
    make_evidence,
    make_line_item,
    make_requirement,
    make_result,
    make_run,
    make_tender,
)


@pytest.fixture
def tenders(db_session: Session) -> TenderRepository:
    return TenderRepository(db_session)


@pytest.fixture
def run(db_session: Session, tenders: TenderRepository) -> Run:
    return RunRepository(db_session).add(make_run(tenders.add(make_tender())))


def test_requirements_keep_order_and_parent_links(db_session: Session, run: Run) -> None:
    parent = make_requirement(run, position=0)
    make_requirement(run, position=2, parent=parent)
    make_requirement(run, position=1, parent=parent)
    db_session.flush()
    db_session.expire_all()

    reqs = RunRepository(db_session).list_requirements(run.id)
    assert [r.position for r in reqs] == [0, 1, 2]
    assert [c.position for c in reqs[0].children] == [1, 2]
    assert reqs[1].parent_id == reqs[0].id
    assert reqs[0].operator is Operator.GTE
    assert reqs[0].kind is ValueKind.NUMERIC
    assert (reqs[0].flags, reqs[0].alternatives, reqs[0].single_firm) == ([], [], False)


def test_each_run_keeps_what_it_read(db_session: Session, tenders: TenderRepository) -> None:
    tender = tenders.add(make_tender())
    runs = RunRepository(db_session)
    first, second = runs.add(make_run(tender)), runs.add(make_run(tender))
    make_requirement(first, position=0, ref="old")
    make_requirement(second, position=0, ref="new")
    db_session.flush()

    assert [r.ref for r in runs.list_requirements(first.id)] == ["old"]
    assert [r.ref for r in runs.list_requirements(second.id)] == ["new"]


def test_a_printed_item_number_or_requirement_id_can_repeat(db_session: Session, run: Run) -> None:
    # Real tenders repeat them; a run must not fail on what a tender prints.
    items = [make_line_item(run, position=n, item_no="1") for n in range(2)]
    for n, item in enumerate(items):
        make_requirement(run, position=n, line_item=item, ref="1.1")
    db_session.flush()
    db_session.expire_all()

    runs = RunRepository(db_session)
    assert [i.item_no for i in runs.list_line_items(run.id)] == ["1", "1"]
    first, second = runs.list_requirements(run.id)
    assert first.ref == second.ref
    assert first.line_item_id != second.line_item_id


def test_a_general_requirement_has_no_line_item(db_session: Session, run: Run) -> None:
    make_requirement(run, position=0)
    db_session.flush()
    assert RunRepository(db_session).list_requirements(run.id)[0].line_item_id is None


def test_run_stores_its_report_as_json(db_session: Session, run: Run) -> None:
    run.report = {"tender": "T", "items": [{"quote": "ø 25 “x”"}]}
    db_session.flush()
    db_session.expire_all()
    assert run.report == {"tender": "T", "items": [{"quote": "ø 25 “x”"}]}


def test_deleting_a_run_removes_everything_it_read(db_session: Session, run: Run) -> None:
    item = make_line_item(run)
    parent = make_requirement(run, position=0, line_item=item)
    child = make_requirement(run, position=1, line_item=item, parent=parent)
    db_session.flush()
    ResultRepository(db_session).add(make_result(run, child))
    runs = RunRepository(db_session)
    run_id = run.id
    runs.delete(run)
    db_session.expire_all()

    assert runs.list_requirements(run_id) == []
    assert runs.list_line_items(run_id) == []
    assert ResultRepository(db_session).list_for_run(run_id) == []


def test_tender_lookup_and_recent_listing(tenders: TenderRepository) -> None:
    older, newer = tenders.add(make_tender()), tenders.add(make_tender())
    assert tenders.get_by_sha256(older.sha256) is older
    assert tenders.list_recent(limit=10) == [newer, older]
    assert tenders.list_recent(limit=1, offset=1) == [older]


def test_run_listing_filters_and_stores_enum_values(db_session: Session) -> None:
    tender = TenderRepository(db_session).add(make_tender())
    runs = RunRepository(db_session)
    queued = runs.add(make_run(tender))
    done = runs.add(make_run(tender, status=RunStatus.DONE))

    assert runs.list_recent(limit=10) == [done, queued]
    assert runs.list_recent(limit=10, status=RunStatus.QUEUED) == [queued]
    other_tender = TenderRepository(db_session).add(make_tender())
    other = runs.add(make_run(other_tender))
    assert runs.list_recent(limit=10, tender_id=other_tender.id) == [other]
    raw = db_session.execute(
        text("SELECT status, manufacturers FROM runs WHERE id = :id"), {"id": queued.id}
    ).one()
    assert raw.status == "queued"
    assert raw.manufacturers == ["AcmePumps"]


def test_results_load_with_evidence_and_are_unique(db_session: Session, run: Run) -> None:
    requirement = make_requirement(run, position=0)
    db_session.flush()
    doc = DocumentRepository(db_session).add(make_document())
    results = ResultRepository(db_session)

    result = make_result(run, requirement)
    result.evidence = [make_evidence(doc, page=1), make_evidence(doc, page=2)]
    results.add(result)
    results.add(make_result(run, requirement, manufacturer="Other"))
    db_session.expire_all()

    loaded = results.list_for_run(run.id, manufacturer="AcmePumps")
    assert len(loaded) == 1
    assert sorted(e.page_number for e in loaded[0].evidence) == [1, 2]
    assert len(results.list_for_run(run.id)) == 2

    with pytest.raises(IntegrityError):
        results.add(make_result(run, requirement))


def test_tied_catalogues_keep_one_row_each(db_session: Session, run: Run) -> None:
    requirement = make_requirement(run, position=0)
    db_session.flush()
    results = ResultRepository(db_session)
    results.add(make_result(run, requirement, catalogue="alpha.pdf", tied=True))
    results.add(make_result(run, requirement, catalogue="beta.pdf", tied=True))
    assert {r.catalogue for r in results.list_for_run(run.id)} == {"alpha.pdf", "beta.pdf"}


def test_a_general_result_has_no_manufacturer_and_is_still_unique(
    db_session: Session, run: Run
) -> None:
    requirement = make_requirement(run, position=0)
    db_session.flush()
    results = ResultRepository(db_session)
    results.add(make_result(run, requirement, manufacturer=None, catalogue=None))
    with pytest.raises(IntegrityError):  # NULLs count as equal here
        results.add(make_result(run, requirement, manufacturer=None, catalogue=None))


def test_cited_document_cannot_be_deleted(db_session: Session, run: Run) -> None:
    requirement = make_requirement(run, position=0)
    db_session.flush()
    docs = DocumentRepository(db_session)
    doc = docs.add(make_document())
    result = make_result(run, requirement)
    result.evidence = [make_evidence(doc)]
    ResultRepository(db_session).add(result)

    with pytest.raises(IntegrityError, match="fk_evidence_document_id_documents"):
        docs.delete(doc)
