"""DocumentRepository, ChunkRepository and SpecFactRepository against Postgres."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models import Chunk, Document, Page, SpecFact
from app.db.repositories import ChunkRepository, DocumentRepository, SpecFactRepository
from tests.integration.factories import make_chunk, make_document, make_spec_fact


@pytest.fixture
def docs(db_session: Session) -> DocumentRepository:
    return DocumentRepository(db_session)


def test_add_assigns_id_and_persists_pages(docs: DocumentRepository) -> None:
    doc = docs.add(make_document(pages=3))
    assert isinstance(doc.id, uuid.UUID)
    assert [p.page_number for p in docs.get_or_raise(doc.id).pages] == [1, 2, 3]


def test_get_by_sha256(docs: DocumentRepository) -> None:
    doc = docs.add(make_document())
    assert docs.get_by_sha256(doc.sha256) is doc
    assert docs.get_by_sha256("0" * 64) is None


def test_duplicate_sha256_is_rejected(docs: DocumentRepository) -> None:
    first = docs.add(make_document())
    duplicate = make_document()
    duplicate.sha256 = first.sha256
    with pytest.raises(IntegrityError):
        docs.add(duplicate)


def test_duplicate_page_number_is_rejected(docs: DocumentRepository) -> None:
    doc = docs.add(make_document(pages=1))
    doc.pages.append(Page(page_number=1, text="again"))
    with pytest.raises(IntegrityError):
        docs.add(doc)


def test_get_or_raise_unknown_id(docs: DocumentRepository) -> None:
    with pytest.raises(NotFoundError, match="Document"):
        docs.get_or_raise(uuid.uuid4())


def test_list_filters_and_manufacturers(docs: DocumentRepository) -> None:
    docs.add_all(
        [
            make_document(manufacturer="Beta"),
            make_document(manufacturer="Acme"),
            make_document(manufacturer="Acme", category="motors"),
        ]
    )
    assert [d.manufacturer for d in docs.find(category="valves_pumps")] == ["Acme", "Beta"]
    assert len(docs.find(manufacturer="Acme")) == 2
    assert docs.list_manufacturers("valves_pumps") == ["Acme", "Beta"]


def test_get_page(docs: DocumentRepository) -> None:
    doc = docs.add(make_document(pages=2))
    page = docs.get_page(doc.id, 2)
    assert page is not None
    assert page.text == "synthetic page 2"
    assert docs.get_page(doc.id, 99) is None


def test_delete_document_cascades_to_pages_and_chunks(
    db_session: Session, docs: DocumentRepository
) -> None:
    doc = docs.add(make_document(pages=1))
    ChunkRepository(db_session).add(make_chunk(doc, doc.pages[0]))
    docs.delete(doc)
    db_session.expire_all()
    assert db_session.scalars(select(Page)).all() == []
    assert db_session.scalars(select(Chunk)).all() == []


def test_chunks_ordered_filtered_and_deleted(db_session: Session, docs: DocumentRepository) -> None:
    chunks = ChunkRepository(db_session)
    acme = docs.add(make_document(manufacturer="Acme", pages=2))
    other = docs.add(make_document(manufacturer="Other", pages=1))
    p1, p2 = acme.pages
    chunks.add_all(
        [
            make_chunk(acme, p2, position=0, text="p2-0"),
            make_chunk(acme, p1, position=1, text="p1-1"),
            make_chunk(acme, p1, position=0, text="p1-0"),
            make_chunk(other, other.pages[0], text="other"),
        ]
    )
    assert [c.raw_text for c in chunks.list_for_document(acme.id)] == ["p1-0", "p1-1", "p2-0"]
    assert chunks.delete_for_document(acme.id) == 3
    assert chunks.list_for_document(acme.id) == []
    assert [c.raw_text for c in chunks.list_for_document(other.id)] == ["other"]


def test_spec_facts_lookup_keeps_conflicting_facts(
    db_session: Session, docs: DocumentRepository
) -> None:
    facts = SpecFactRepository(db_session)
    doc = docs.add(make_document())
    first = make_spec_fact(doc, attribute="max_pressure", value_text="16 bar", page=1)
    second = make_spec_fact(doc, attribute="max_pressure", value_text="10 bar", page=2)
    second.conflict = first.conflict = True
    facts.add_all([first, second, make_spec_fact(doc, attribute="body_material", value_text="X")])

    found = facts.find(manufacturer="AcmePumps", category="valves_pumps", attribute="max_pressure")
    assert [f.value_text for f in found] == ["16 bar", "10 bar"]
    assert all(f.conflict for f in found)
    assert (
        facts.find(manufacturer="Nobody", category="valves_pumps", attribute="max_pressure") == []
    )
    assert len(facts.list_for_document(doc.id)) == 3


def _tagged_fact(
    db_session: Session, doc: Document, product_type: str | None, value: str
) -> SpecFact:
    page = doc.pages[0]
    chunk = make_chunk(doc, page, position=len(doc.chunks), text=value)
    chunk.product_type = product_type
    db_session.add(chunk)
    db_session.flush()
    fact = make_spec_fact(doc, attribute="nominal_size", value_text=value)
    fact.chunk_id, fact.product_type = chunk.id, product_type
    return fact


def test_spec_facts_product_filter_keeps_only_that_product(
    db_session: Session, docs: DocumentRepository
) -> None:
    facts = SpecFactRepository(db_session)
    valves = make_document()
    valves.product_types = ["gate_valve", "globe_valve"]
    pumps = make_document()
    pumps.product_types = ["centrifugal_pump"]
    docs.add(valves)
    docs.add(pumps)
    facts.add_all(
        [
            _tagged_fact(db_session, valves, "gate_valve", "gate 50"),
            _tagged_fact(db_session, valves, "globe_valve", "globe 80"),
            _tagged_fact(db_session, valves, None, "general 100"),
            _tagged_fact(db_session, pumps, None, "pump 65"),
        ]
    )

    def values(product_type: str | None) -> set[str]:
        found = facts.find(
            manufacturer="AcmePumps",
            category="valves_pumps",
            attribute="nominal_size",
            product_type=product_type,
        )
        return {f.value_text for f in found}

    assert values("gate_valve") == {"gate 50", "general 100"}
    assert values("globe_valve") == {"globe 80", "general 100"}
    assert values("centrifugal_pump") == {"pump 65"}
    assert values("butterfly_valve") == set()
    assert len(values(None)) == 4


def test_spec_facts_follow_their_chunk_after_retagging(
    db_session: Session, docs: DocumentRepository
) -> None:
    facts = SpecFactRepository(db_session)
    doc = make_document()
    docs.add(doc)
    fact = _tagged_fact(db_session, doc, None, "size 50")
    facts.add_all([fact])
    [chunk] = doc.chunks
    chunk.product_type, chunk.product_sub_type = "gate_valve", "knife_gate"
    db_session.flush()

    assert facts.sync_products_from_chunks(doc.id) == 1
    assert facts.sync_products_from_chunks(doc.id) == 0
    [row] = facts.list_for_document(doc.id)
    assert (row.product_type, row.product_sub_type) == ("gate_valve", "knife_gate")


def test_withdrawn_listings_are_left_out_unless_asked(docs: DocumentRepository) -> None:
    kept, withdrawn = make_document(), make_document()
    for doc in (kept, withdrawn):
        doc.auto_sourced = True
    withdrawn.withdrawn_reason = "names flow control valve, not control valve"
    docs.add_all([kept, withdrawn, make_document()])
    assert docs.list_auto_sourced() == [kept]
    assert docs.list_auto_sourced(include_withdrawn=True) == [kept, withdrawn]
