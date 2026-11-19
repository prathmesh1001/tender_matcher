"""Retriever against PostgreSQL + pgvector: product filter -> hybrid search -> rerank."""

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.models.embedding import EMBEDDING_DIMENSIONS
from app.db.repositories import ChunkRepository, EmbeddingRepository
from app.ontology.loader import load_ontologies
from app.retrieval.bm25_index import Bm25Params
from app.retrieval.hybrid_search import FusionParams
from app.retrieval.query_builder import QueryVocabulary
from app.retrieval.retriever import RetrievalParams, Retriever
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.retrieval.vector_index import embed_document_chunks
from app.schemas.retrieval import RetrievalOutcome, RetrievalRequest
from tests.integration.factories import CATEGORY, make_chunk, make_document

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"


class FakeEmbedder:
    """Unit vectors derived from the text hash (deterministic, no model)."""

    model_name = "fake-1024"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        raw = [((seed[i % 32] + i) % 17) - 8.0 for i in range(EMBEDDING_DIMENSIONS)]
        norm = math.sqrt(sum(v * v for v in raw))
        return [v / norm for v in raw]


class KeywordReranker:
    """Scores 0.9 when the passage contains the keyword, else 0.1."""

    model_name = "fake-reranker"

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword
        self.passages: list[str] = []

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        self.passages = list(passages)
        return [0.9 if self.keyword in p else 0.1 for p in passages]


@pytest.fixture(scope="module")
def vocabularies() -> tuple[SearchVocabulary, QueryVocabulary]:
    ontologies = load_ontologies(ONTOLOGY_DIR)
    search = SearchVocabulary.from_ontologies(ontologies)
    return search, QueryVocabulary.from_ontologies(ontologies, search, max_distance=2, min_length=5)


def _doc(
    session: Session,
    search: SearchVocabulary,
    declared: list[str],
    chunks: list[tuple[str, str | None]],
    manufacturer: str = "Acme",
) -> Document:
    doc = make_document(manufacturer=manufacturer, pages=1)
    doc.product_types = declared
    session.add(doc)
    session.flush()
    for i, (text, product_type) in enumerate(chunks):
        chunk = make_chunk(doc, doc.pages[0], position=i, text=text)
        chunk.search_text = normalize_for_search(text, search)
        chunk.product_type = product_type
        session.add(chunk)
    session.flush()
    embed_document_chunks(session, FakeEmbedder(), doc.id)
    return doc


def _retriever(
    session: Session, vocab: QueryVocabulary, reranker: KeywordReranker, top_k: int = 2
) -> Retriever:
    params = RetrievalParams(
        bm25=Bm25Params(k1=1.5, b=0.75),
        fusion=FusionParams(k=60, bm25_weight=1.0, dense_weight=0.7),
        candidates=50,
        top_k=top_k,
        min_score=0.3,
    )
    return Retriever(
        chunks=ChunkRepository(session),
        embeddings=EmbeddingRepository(session),
        embedder=FakeEmbedder(),
        reranker=reranker,
        vocabulary=vocab,
        params=params,
    )


def _request(text: str, product_type: str = "check_valve") -> RetrievalRequest:
    return RetrievalRequest(
        manufacturer="Acme", category=CATEGORY, product_type=product_type, text=text
    )


CHECK_DOC = [
    ("Swing check valve PN16 flanged, body WCB", "check_valve"),
    ("Dual plate check valve PN25 wafer", "check_valve"),
    ("Hydro test as per API 598", None),
    ("Ball valve PN16 full bore", "ball_valve"),
]


def test_returns_reranked_evidence_from_the_product_only(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    search, vocab = vocabularies
    _doc(db_session, search, ["check_valve", "ball_valve"], CHECK_DOC)
    _doc(db_session, search, ["check_valve"], [("Check valve PN16", "check_valve")], "Other")
    reranker = KeywordReranker("PN16")

    result = _retriever(db_session, vocab, reranker).retrieve(_request("NRV PN 16 flanged"))

    assert result.outcome == RetrievalOutcome.EVIDENCE
    assert result.candidates_searched == 3  # two check chunks + the general one, not the ball
    assert all("Ball" not in p and "Other" not in p for p in reranker.passages)
    top = ChunkRepository(db_session).get(result.hits[0].chunk_id)
    assert top is not None
    assert top.raw_text.startswith("Swing check valve PN16")
    assert len(result.hits) == 2
    assert result.hits[0].rerank_score >= result.hits[1].rerank_score
    assert result.query is not None
    assert "check" in result.query.expansion_terms


def test_no_document_for_the_product_type(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    search, vocab = vocabularies
    _doc(db_session, search, ["check_valve", "ball_valve"], CHECK_DOC)
    result = _retriever(db_session, vocab, KeywordReranker("x")).retrieve(
        _request("butterfly valve PN16", product_type="butterfly_valve")
    )
    assert result.outcome == RetrievalOutcome.NO_PRODUCT_DOCUMENTS
    assert result.hits == ()


def test_low_rerank_score_is_insufficient_evidence(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    search, vocab = vocabularies
    _doc(db_session, search, ["check_valve", "ball_valve"], CHECK_DOC)
    result = _retriever(db_session, vocab, KeywordReranker("nothing-matches")).retrieve(
        _request("check valve PN40")
    )
    assert result.outcome == RetrievalOutcome.BELOW_THRESHOLD
    assert result.hits  # still returned, for the report and review


def test_distances_are_exact_inside_the_filter(db_session: Session) -> None:
    embedder = FakeEmbedder()
    repo = EmbeddingRepository(db_session)
    doc = make_document(pages=1)
    db_session.add(doc)
    db_session.flush()
    for i, text in enumerate(["seat PTFE", "body WCB", "stem 17-4PH"]):
        db_session.add(make_chunk(doc, doc.pages[0], position=i, text=text))
    db_session.flush()
    embed_document_chunks(db_session, embedder, doc.id)
    chunks = ChunkRepository(db_session).list_for_document(doc.id)
    wanted = {c.embed_hash for c in chunks[:2] if c.embed_hash}

    distances = repo.distances_to(embedder.model_name, embedder.embed(["seat PTFE"])[0], wanted)

    assert set(distances) == wanted
    assert distances[chunks[0].embed_hash or ""] == pytest.approx(0.0, abs=1e-6)
    assert repo.distances_to(embedder.model_name, [0.0] * EMBEDDING_DIMENSIONS, set()) == {}


BUTTERFLY_DOC: list[tuple[str, str | None]] = [
    ("Design: BS EN 593", "butterfly_valve"),
    ("Body: ASTM A126 Gr B / ASTM A216 Gr WCB", "butterfly_valve"),
    ("Disc: ASTM A536 Gr. 65-45-12 + Nylon 12 Coated", "butterfly_valve"),
    ("Seat: EPDM / BUNA-N", "butterfly_valve"),
]


@pytest.mark.parametrize(
    ("text", "attribute", "expected_top", "code"),
    [
        ("DESIGN/MFG STD- EN593", "design_standard", "Design: BS EN 593", "en593"),
        ("Body- CI", "body_material", "Body: ASTM A126", "a126"),  # CI = A126 (equivalents)
        ("Disc- SGI", "disc_material", "Disc: ASTM A536", "a536"),  # SGI = A536
    ],
)
def test_exact_code_is_evidence_despite_a_low_rerank_score(
    db_session: Session,
    vocabularies: tuple[SearchVocabulary, QueryVocabulary],
    text: str,
    attribute: str,
    expected_top: str,
    code: str,
) -> None:
    search, vocab = vocabularies
    _doc(db_session, search, ["butterfly_valve"], BUTTERFLY_DOC)
    request = RetrievalRequest(
        manufacturer="Acme",
        category=CATEGORY,
        product_type="butterfly_valve",
        text=text,
        attribute=attribute,
    )
    # The reranker scores everything 0.1 (below the 0.3 threshold), as it did on Delval.
    result = _retriever(db_session, vocab, KeywordReranker("nothing-matches")).retrieve(request)

    assert result.outcome == RetrievalOutcome.EVIDENCE
    top = ChunkRepository(db_session).get(result.hits[0].chunk_id)
    assert top is not None
    assert top.raw_text.startswith(expected_top)
    assert code in result.hits[0].code_matches
    assert result.hits[0].rerank_score == pytest.approx(0.1)  # low score kept for confidence


def test_a_bare_number_is_never_a_code_match(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    # B4: "450" inside a size table is the comparator's job, not evidence by text match.
    search, vocab = vocabularies
    _doc(db_session, search, ["butterfly_valve"], [("Valve Size: 450; A: 114", "butterfly_valve")])
    request = RetrievalRequest(
        manufacturer="Acme",
        category=CATEGORY,
        product_type="butterfly_valve",
        text="Valve Size - 450MM",
        attribute="nominal_size",
    )
    result = _retriever(db_session, vocab, KeywordReranker("nothing-matches")).retrieve(request)
    assert result.outcome == RetrievalOutcome.BELOW_THRESHOLD
    assert result.hits[0].code_matches == ()


def test_hnsw_matches_exact_inside_the_filter_and_restores_settings(db_session: Session) -> None:
    from sqlalchemy import func, select

    embedder = FakeEmbedder()
    repo = EmbeddingRepository(db_session)
    doc = make_document(pages=1)
    db_session.add(doc)
    db_session.flush()
    texts = [f"row {i} seat PTFE body WCB" for i in range(30)]
    for i, text in enumerate(texts):
        db_session.add(make_chunk(doc, doc.pages[0], position=i, text=text))
    db_session.flush()
    embed_document_chunks(db_session, embedder, doc.id)
    chunks = ChunkRepository(db_session).list_for_document(doc.id)
    wanted = {c.embed_hash for c in chunks[::3] if c.embed_hash}  # a filter: every 3rd text
    query = embedder.embed([texts[3]])[0]

    exact = repo.distances_to(embedder.model_name, query, wanted)
    hnsw = repo.nearest_hnsw(embedder.model_name, query, wanted, limit=5, ef_search=100)

    assert set(hnsw) <= wanted  # never a vector outside the filter
    assert len(hnsw) == 5
    nearest_exact = sorted(exact, key=lambda h: exact[h])[:5]
    assert set(hnsw) == set(nearest_exact)
    assert db_session.scalar(select(func.current_setting("enable_seqscan"))) == "on"


def test_search_can_be_limited_to_one_catalogue(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    search, vocab = vocabularies
    wanted = _doc(db_session, search, ["check_valve"], [("Check valve PN16", "check_valve")])
    _doc(db_session, search, ["check_valve"], [("Check valve PN25", "check_valve")])
    reranker = KeywordReranker("PN")
    request = RetrievalRequest(
        manufacturer="Acme",
        category=CATEGORY,
        product_type="check_valve",
        text="check valve",
        document_sha256=wanted.sha256,
    )

    result = _retriever(db_session, vocab, reranker).retrieve(request)

    assert result.candidates_searched == 1
    assert reranker.passages == ["Check valve PN16"]


def test_evidence_hits_keep_only_accepted_hits(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    search, vocab = vocabularies
    _doc(
        db_session,
        search,
        ["check_valve"],
        [("Check valve flanged", "check_valve"), ("Check valve wafer", "check_valve")],
    )
    retriever = _retriever(db_session, vocab, KeywordReranker("flanged"))

    result = retriever.retrieve(_request("check valve"))

    assert len(result.hits) == 2
    assert [h.rerank_score for h in retriever.evidence_hits(result)] == [0.9]


def test_evidence_hits_are_empty_below_the_threshold(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    search, vocab = vocabularies
    _doc(db_session, search, ["check_valve"], [("Check valve", "check_valve")])
    retriever = _retriever(db_session, vocab, KeywordReranker("nothing-matches"))

    result = retriever.retrieve(_request("check valve"))

    assert result.outcome == RetrievalOutcome.BELOW_THRESHOLD
    assert retriever.evidence_hits(result) == ()


class CountingEmbedder(FakeEmbedder):
    """FakeEmbedder that records each call's texts."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return super().embed(texts)


def test_retrieve_many_equals_one_by_one_with_one_embedder_call(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    search, vocab = vocabularies
    _doc(db_session, search, ["check_valve", "ball_valve"], CHECK_DOC)
    _doc(db_session, search, ["check_valve"], [("Check valve PN16", "check_valve")], "Other")
    requests = [
        _request("NRV PN 16 flanged"),
        _request("check valve PN40"),
        _request("NRV PN 16 flanged"),  # a repeated query is embedded once
        _request("butterfly valve PN16", product_type="butterfly_valve"),
        RetrievalRequest(
            manufacturer="Other", category=CATEGORY, product_type="check_valve", text="PN16"
        ),
    ]
    single = [_retriever(db_session, vocab, KeywordReranker("PN16")).retrieve(r) for r in requests]
    embedder = CountingEmbedder()
    batched = _retriever(db_session, vocab, KeywordReranker("PN16"))
    batched = Retriever(
        chunks=batched.chunks,
        embeddings=batched.embeddings,
        embedder=embedder,
        reranker=batched.reranker,
        vocabulary=batched.vocabulary,
        params=batched.params,
    )

    results = batched.retrieve_many(requests)

    assert results == single
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == len(set(embedder.calls[0])) == 3


def test_retrieve_many_of_nothing_calls_no_model(
    db_session: Session, vocabularies: tuple[SearchVocabulary, QueryVocabulary]
) -> None:
    _, vocab = vocabularies
    retriever = _retriever(db_session, vocab, KeywordReranker("x"))
    embedder = CountingEmbedder()
    retriever = Retriever(
        chunks=retriever.chunks,
        embeddings=retriever.embeddings,
        embedder=embedder,
        reranker=retriever.reranker,
        vocabulary=retriever.vocabulary,
        params=retriever.params,
    )
    assert retriever.retrieve_many([]) == []
    assert embedder.calls == []
