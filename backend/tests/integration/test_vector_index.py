"""Vector indexing against PostgreSQL + pgvector with a deterministic fake embedder."""

import hashlib
import math
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models.embedding import EMBEDDING_DIMENSIONS
from app.db.repositories import ChunkRepository
from app.retrieval.embedder import embedding_text, text_hash
from app.retrieval.vector_index import embed_document_chunks
from app.schemas.enums import DocumentSource
from tests.integration.factories import make_chunk, make_document


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
