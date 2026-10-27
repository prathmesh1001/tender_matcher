"""The request-scoped session dependency commits on success and rolls back on error."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.deps import DbSessionDep
from app.core.exceptions import InvalidInputError
from app.db.repositories import TenderRepository
from tests.integration.factories import make_tender


@pytest.fixture
def db_client(app: FastAPI, db_engine: Engine) -> Iterator[TestClient]:
    router = APIRouter()

    @router.post("/_test/tenders/{sha}")
    def create(sha: str, session: DbSessionDep) -> dict[str, str]:
        tender = make_tender()
        tender.sha256 = sha
        TenderRepository(session).add(tender)
        return {"id": str(tender.id)}

    @router.post("/_test/tenders/{sha}/fail")
    def create_then_fail(sha: str, session: DbSessionDep) -> None:
        tender = make_tender()
        tender.sha256 = sha
        TenderRepository(session).add(tender)
        raise InvalidInputError("rejected after insert")

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _count(engine: Engine, sha: str) -> int:
    with engine.connect() as conn:
        return int(conn.scalar(text("SELECT count(*) FROM tenders WHERE sha256 = :s"), {"s": sha}))


def _cleanup(engine: Engine, sha: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tenders WHERE sha256 = :s"), {"s": sha})


def test_successful_request_commits(db_client: TestClient, db_engine: Engine) -> None:
    sha = uuid.uuid4().hex
    try:
        assert db_client.post(f"/_test/tenders/{sha}").status_code == 200
        assert _count(db_engine, sha) == 1
    finally:
        _cleanup(db_engine, sha)


def test_failed_request_rolls_back(db_client: TestClient, db_engine: Engine) -> None:
    sha = uuid.uuid4().hex
    try:
        assert db_client.post(f"/_test/tenders/{sha}/fail").status_code == 422
        assert _count(db_engine, sha) == 0
    finally:
        _cleanup(db_engine, sha)
