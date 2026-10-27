"""TenderRepository, RunRepository and ResultRepository against Postgres."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.repositories import (
    DocumentRepository,
    ResultRepository,
    RunRepository,
    TenderRepository,
)
from app.schemas.enums import Operator, RunStatus
from tests.integration.factories import (
    make_document,
    make_evidence,
    make_requirement,
    make_result,
    make_run,
    make_tender,
)


@pytest.fixture
def tenders(db_session: Session) -> TenderRepository:
    return TenderRepository(db_session)


def test_requirements_keep_order_and_parent_links(
    db_session: Session, tenders: TenderRepository
) -> None:
    tender = make_tender()
    parent = make_requirement(tender, position=0)
    make_requirement(tender, position=2, parent=parent)
    make_requirement(tender, position=1, parent=parent)
    tenders.add(tender)
    db_session.expire_all()

    reqs = tenders.list_requirements(tender.id)
    assert [r.position for r in reqs] == [0, 1, 2]
    assert [c.position for c in reqs[0].children] == [1, 2]
    assert reqs[1].parent_id == reqs[0].id
    assert reqs[0].operator is Operator.GTE


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


def test_results_load_with_evidence_and_are_unique(db_session: Session) -> None:
    tender = make_tender()
    requirement = make_requirement(tender, position=0)
    TenderRepository(db_session).add(tender)
    doc = DocumentRepository(db_session).add(make_document())
    run = RunRepository(db_session).add(make_run(tender))
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


def test_cited_document_cannot_be_deleted(db_session: Session) -> None:
    tender = make_tender()
    requirement = make_requirement(tender, position=0)
    TenderRepository(db_session).add(tender)
    docs = DocumentRepository(db_session)
    doc = docs.add(make_document())
    run = RunRepository(db_session).add(make_run(tender))
    result = make_result(run, requirement)
    result.evidence = [make_evidence(doc)]
    ResultRepository(db_session).add(result)

    with pytest.raises(IntegrityError, match="fk_evidence_document_id_documents"):
        docs.delete(doc)
