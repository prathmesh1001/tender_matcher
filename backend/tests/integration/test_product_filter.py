"""Retrieval filter: product-type chunks plus general chunks of documents covering the type."""

from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.repositories import ChunkRepository
from tests.integration.factories import CATEGORY, make_chunk, make_document


def _doc(
    session: Session,
    manufacturer: str,
    declared: list[str],
    chunks: list[tuple[str, str | None]],
) -> Document:
    doc = make_document(manufacturer=manufacturer, pages=1)
    doc.product_types = declared
    session.add(doc)
    session.flush()
    for i, (text, product_type) in enumerate(chunks):
        chunk = make_chunk(doc, doc.pages[0], position=i, text=text)
        chunk.product_type = product_type
        session.add(chunk)
    session.flush()
    return doc


def test_filter_keeps_type_and_general_chunks_of_covering_documents(db_session: Session) -> None:
    _doc(
        db_session,
        "Acme",
        ["gate_valve", "check_valve"],
        [
            ("gate body WCB", "gate_valve"),
            ("check disc CF8M", "check_valve"),
            ("API 598 test", None),
        ],
    )
    _doc(db_session, "Acme", ["ball_valve"], [("ball seat PTFE", "ball_valve"), ("ISO 9001", None)])
    _doc(db_session, "Other", ["gate_valve"], [("other gate", "gate_valve")])

    repo = ChunkRepository(db_session)
    gate = [c.raw_text for c in repo.list_for_product("Acme", CATEGORY, "gate_valve")]
    ball = [c.raw_text for c in repo.list_for_product("Acme", CATEGORY, "ball_valve")]
    assert sorted(gate) == ["API 598 test", "gate body WCB"]  # never the check or ball chunks
    assert sorted(ball) == ["ISO 9001", "ball seat PTFE"]
    assert repo.list_for_product("Acme", CATEGORY, "butterfly_valve") == []
