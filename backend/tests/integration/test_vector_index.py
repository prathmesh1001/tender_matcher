"""Vector indexing against PostgreSQL + pgvector with a deterministic fake embedder."""

import hashlib
import math
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.models.embedding import EMBEDDING_DIMENSIONS
from app.db.repositories import ChunkRepository
from app.retrieval.embedder import embedding_text, text_hash
from app.retrieval.vector_index import embed_document_chunks
from app.schemas.enums import DocumentSource
from app.schemas.retrieval import DocumentIndexStatus
from tests.integration.factories import make_chunk, make_document, make_spec_fact


class FakeEmbedder:
    """Unit vectors derived from the text hash; counts how many texts it embedded."""

    model_name = "fake-1024"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.extend(texts)
        return [self._vector(t) for t in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        raw = [((seed[i % 32] + i) % 17) - 8.0 for i in range(EMBEDDING_DIMENSIONS)]
        norm = math.sqrt(sum(v * v for v in raw))
        return [v / norm for v in raw]


def _doc_with_chunks(session: Session, texts: list[str], manufacturer: str = "AcmePumps"):  # type: ignore[no-untyped-def]
    doc = make_document(manufacturer=manufacturer, pages=1)
    session.add(doc)
    session.flush()
    for i, text in enumerate(texts):
        session.add(make_chunk(doc, doc.pages[0], position=i, text=text))
    session.flush()
    return doc


def test_duplicate_texts_are_embedded_once(db_session: Session) -> None:
    doc = _doc_with_chunks(db_session, ["Body: SS316", "Body: SS316", "Seat: PTFE"])
    embedder = FakeEmbedder()
    stats = embed_document_chunks(db_session, embedder, doc.id)
    assert (stats.chunks, stats.distinct_texts, stats.embedded) == (3, 2, 2)
    assert len(embedder.calls) == 2
    chunks = ChunkRepository(db_session).list_for_document(doc.id)
    assert chunks[0].embed_hash == chunks[1].embed_hash == text_hash(embedding_text("Body: SS316"))


def test_vectors_are_shared_across_documents_and_runs(db_session: Session) -> None:
    first = _doc_with_chunks(db_session, ["Body: SS316", "Seat: PTFE"])
    second = _doc_with_chunks(db_session, ["Seat: PTFE", "Stem: 17-4PH"], manufacturer="Other")
    embedder = FakeEmbedder()
    embed_document_chunks(db_session, embedder, first.id)
    stats = embed_document_chunks(db_session, embedder, second.id)
    assert (stats.embedded, stats.reused) == (1, 1)
    again = embed_document_chunks(db_session, embedder, first.id)
    assert again.embedded == 0
    assert len(embedder.calls) == 3


def test_documents_needing_embedding(db_session: Session) -> None:
    done = _doc_with_chunks(db_session, ["Body: SS316"])
    todo = _doc_with_chunks(db_session, ["Seat: PTFE"], manufacturer="Other")
    embedder = FakeEmbedder()
    embed_document_chunks(db_session, embedder, done.id)
    pending = ChunkRepository(db_session).documents_needing_embedding(
        embedder.model_name, source_kind=DocumentSource.MANUFACTURER
    )
    ids = {doc_id for doc_id, _ in pending}
    assert todo.id in ids
    assert done.id not in ids
    # A different model has no vectors yet, so both documents still need it.
    other_model = {d for d, _ in ChunkRepository(db_session).documents_needing_embedding("x")}
    assert {done.id, todo.id} <= other_model


def _status(db_session: Session, doc: Document, model: str) -> DocumentIndexStatus:
    rows = ChunkRepository(db_session).index_status(model)
    return next(r for r in rows if r.file_path == doc.file_path)


def test_index_status_counts_chunks_vectors_and_facts(db_session: Session) -> None:
    doc = _doc_with_chunks(db_session, ["Body: SS316", "Body: SS316", "Seat: PTFE"], "Counted")
    embedder = FakeEmbedder()
    assert _status(db_session, doc, embedder.model_name).with_vector == 0
    embed_document_chunks(db_session, embedder, doc.id)
    db_session.add(make_spec_fact(doc, attribute="body_material", value_text="SS316"))
    db_session.flush()
    row = _status(db_session, doc, embedder.model_name)
    # Facts are counted once, not once per chunk the join returns.
    assert (row.chunks, row.with_vector, row.spec_facts) == (3, 3, 1)
    assert _status(db_session, doc, "another-model").with_vector == 0


def test_index_status_lists_a_catalogue_without_chunks(db_session: Session) -> None:
    doc = make_document(manufacturer="Empty", pages=1)
    db_session.add(doc)
    db_session.flush()
    row = _status(db_session, doc, FakeEmbedder.model_name)
    assert (row.chunks, row.with_vector, row.spec_facts) == (0, 0, 0)


def test_index_status_leaves_out_tender_documents(db_session: Session) -> None:
    doc = make_document(manufacturer="Tendered", pages=1)
    doc.source_kind = DocumentSource.TENDER
    doc.manufacturer = None
    doc.category = None
    db_session.add(doc)
    db_session.flush()
    rows = ChunkRepository(db_session).index_status(FakeEmbedder.model_name)
    assert doc.file_path not in {r.file_path for r in rows}
