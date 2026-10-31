"""Spec extraction against the real database, with a fake LLM. All text is synthetic."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError
from app.db.models import Document
from app.db.repositories import SpecFactRepository
from app.ingestion.spec_extractor import ExtractionOptions
from app.ontology.loader import load_ontologies
from app.schemas.spec_facts import DropReason, ExtractedFact, ExtractedFacts
from app.services.spec_service import SpecExtractionService
from tests.integration.factories import make_chunk, make_document

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"
TEXT = "Body A 216 Gr. WCB"
GOOD = ExtractedFact(chunk=0, attribute="body_material", label="Body", value="A 216 Gr. WCB")
BAD = ExtractedFact(chunk=0, attribute="body_material", label="Body", value="invented 999")


class _LLM:
    """Always proposes the given facts, or fails; counts its calls."""

    model_name = "fake"

    def __init__(self, facts: list[ExtractedFact], *, fail: bool = False) -> None:
        self.facts = facts
        self.fail = fail
        self.calls = 0

    def generate_json[T: BaseModel](self, prompt: str, schema: type[T]) -> T:
        self.calls += 1
        if self.fail:
            raise ExternalServiceError("down")
        return schema.model_validate(ExtractedFacts(facts=self.facts).model_dump())


@pytest.fixture
def factory(db_connection: Connection) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def document(factory: sessionmaker[Session]) -> Document:
    with factory() as session:
        doc = make_document(pages=1)
        doc.pages[0].text = f"Materials\n{TEXT}"
        session.add(doc)
        session.flush()
        session.add(make_chunk(doc, doc.pages[0], text=TEXT))
        session.commit()
        return doc


def _service(
    factory: sessionmaker[Session], settings: Settings, llm: _LLM
) -> SpecExtractionService:
    return SpecExtractionService(
        session_factory=factory,
        llm=llm,
        ontologies={o.category: o for o in load_ontologies(ONTOLOGY_DIR)},
        options=ExtractionOptions.from_settings(settings),
    )


def _stored(factory: sessionmaker[Session], document: Document) -> list[tuple[str, int, str]]:
    with factory() as session:
        rows = SpecFactRepository(session).list_for_document(document.id)
        return [(r.value_text, r.page_number, r.quote) for r in rows]


def test_verified_facts_are_stored_and_invented_ones_dropped(
    factory: sessionmaker[Session], settings: Settings, document: Document
) -> None:
    result = _service(factory, settings, _LLM([GOOD, BAD])).extract()
    [outcome] = result.succeeded
    assert (outcome.proposed, outcome.stored) == (2, 1)
    assert outcome.dropped == {DropReason.NOT_IN_CHUNK: 1}
    assert _stored(factory, document) == [("A 216 Gr. WCB", 1, "Body | A 216 Gr. WCB")]
    with factory() as session:
        [row] = SpecFactRepository(session).find(
            manufacturer="AcmePumps", category="valves_pumps", attribute="body_material"
        )
        assert row.chunk_id is not None


def test_rerun_skips_stored_documents_and_never_duplicates(
    factory: sessionmaker[Session], settings: Settings, document: Document
) -> None:
    _service(factory, settings, _LLM([GOOD])).extract()
    again = _LLM([GOOD])
    assert _service(factory, settings, again).extract().succeeded == []
    assert again.calls == 0
    assert len(_stored(factory, document)) == 1


def test_reprocess_replaces_facts(
    factory: sessionmaker[Session], settings: Settings, document: Document
) -> None:
    _service(factory, settings, _LLM([GOOD])).extract()
    assert _service(factory, settings, _LLM([GOOD])).extract(reprocess=True).succeeded
    assert len(_stored(factory, document)) == 1


def test_llm_failure_is_reported_and_keeps_stored_facts(
    factory: sessionmaker[Session], settings: Settings, document: Document
) -> None:
    _service(factory, settings, _LLM([GOOD])).extract()
    result = _service(factory, settings, _LLM([], fail=True)).extract(reprocess=True)
    assert [item for item, _ in result.failed] == [document.file_path]
    assert len(_stored(factory, document)) == 1


def test_manufacturer_filter(
    factory: sessionmaker[Session], settings: Settings, document: Document
) -> None:
    llm = _LLM([GOOD])
    assert _service(factory, settings, llm).extract(manufacturer="Nobody").succeeded == []
    assert llm.calls == 0
