"""Retrieve evidence chunks for one requirement x manufacturer (CLAUDE.md section 7, steps 1-4).

1. Product filter: only the manufacturer's chunks for the product type (plus untagged chunks
   of documents declaring it). Nothing for the type -> NO_PRODUCT_DOCUMENTS.
2. Build the query against the filtered chunks' vocabulary (typo fixes never touch their words).
3. BM25 and dense search inside the filtered set, fused with weighted RRF.
4. Cross-encoder rerank of the fused candidates -> top-k. A hit is accepted if its score is at
   least the threshold, or if it contains one of the requirement's codes ("en593", or "a536"
   for SGI via ontology equivalents): the reranker scores exact spec lines low (measured
   0.01-0.17 on correct Delval chunks). Accepted hits come first. No accepted hit ->
   BELOW_THRESHOLD (insufficient evidence; matching skips the LLM). A code-only acceptance
   keeps its low score, so confidence (Step 10) still goes down for it.

The structured `spec_facts` lookup (step 2 of section 7) is done by matching, not here.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import DenseSearch, Settings
from app.db.models import Chunk
from app.db.repositories import ChunkRepository, EmbeddingRepository
from app.retrieval.bm25_index import Bm25Index, Bm25Params
from app.retrieval.embedder import Embedder, embedding_text
from app.retrieval.hybrid_search import FusionParams, dense_ranking, rrf_fuse
from app.retrieval.query_builder import QueryVocabulary, build_query
from app.retrieval.reranker import Reranker
from app.schemas.retrieval import (
    FusedCandidate,
    RetrievalHit,
    RetrievalOutcome,
    RetrievalRequest,
    RetrievalResult,
    SearchQuery,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalParams:
    """Every retrieval knob, read from config."""

    bm25: Bm25Params
    fusion: FusionParams
    candidates: int
    top_k: int
    min_score: float
    dense_search: DenseSearch = DenseSearch.EXACT
    hnsw_ef_search: int = 100

    @classmethod
    def from_settings(cls, settings: Settings) -> RetrievalParams:
        """Collect the retrieval settings."""
        return cls(
            bm25=Bm25Params(k1=settings.BM25_K1, b=settings.BM25_B),
            fusion=FusionParams(
                k=settings.RRF_K,
                bm25_weight=settings.RRF_BM25_WEIGHT,
                dense_weight=settings.RRF_DENSE_WEIGHT,
            ),
            candidates=settings.RETRIEVAL_CANDIDATES,
            top_k=settings.RERANK_TOP_K,
            min_score=settings.RERANK_MIN_SCORE,
            dense_search=settings.DENSE_SEARCH,
            hnsw_ef_search=settings.HNSW_EF_SEARCH,
        )


# The filtered chunks of one manufacturer x product (x document) and their BM25 index.
type _ChunkSet = tuple[list[Chunk], Bm25Index]
type _SetKey = tuple[str, str, str, str | None]


@dataclass(frozen=True, slots=True)
class _Prepared:
    """A search up to the point where it needs its query embedding."""

    request: RetrievalRequest
    chunks: list[Chunk]
    index: Bm25Index
    query: SearchQuery
    dense: bool  # the dense ranking takes part (weight > 0 and the chunks have vectors)


@dataclass(frozen=True, slots=True)
class Retriever:
    """Product filter -> hybrid search -> rerank, for one manufacturer at a time."""

    chunks: ChunkRepository
    embeddings: EmbeddingRepository
    embedder: Embedder
    reranker: Reranker
    vocabulary: QueryVocabulary
    params: RetrievalParams

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Find the best evidence chunks for one requirement.

        Args:
            request: Requirement text, manufacturer, category and product type.

        Returns:
            The outcome, the query used and up to `top_k` reranked hits (best first).

        Raises:
            ExternalServiceError: If the embedder or reranker fails.
        """
        return self.retrieve_many([request])[0]

    def retrieve_many(self, requests: Sequence[RetrievalRequest]) -> list[RetrievalResult]:
        """`retrieve` for several requirements, with the same results, in far less time.

        Requests for the same manufacturer and product share one chunk list and BM25 index,
        and every query is embedded in one embedder call (one by one, the model is called once
        per requirement, measured 0.9 s each on CPU).

        Args:
            requests: The searches, in any order.

        Returns:
            One result per request, in the same order.

        Raises:
            ExternalServiceError: If the embedder or reranker fails.
        """
        sets: dict[_SetKey, _ChunkSet] = {}
        prepared = [self._prepare(r, sets) for r in requests]
        texts = sorted(
            {p.query.dense_text for p in prepared if isinstance(p, _Prepared) and p.dense}
        )
        vectors = dict(zip(texts, self.embedder.embed(texts), strict=True)) if texts else {}
        return [
            (
                p
                if isinstance(p, RetrievalResult)
                else self._finish(p, vectors.get(p.query.dense_text))
            )
            for p in prepared
        ]

    def _chunk_set(self, request: RetrievalRequest, sets: dict[_SetKey, _ChunkSet]) -> _ChunkSet:
        """The request's filtered chunks and their index, read once per manufacturer x product."""
        key = (
            request.manufacturer,
            request.category,
            request.product_type,
            request.document_sha256,
        )
        if key not in sets:
            chunks = self.chunks.list_for_product(*key[:3], sha256=key[3])
            sets[key] = (
                chunks,
                Bm25Index.build([(c.id, c.search_text) for c in chunks], self.params.bm25),
            )
        return sets[key]

    def _prepare(
        self, request: RetrievalRequest, sets: dict[_SetKey, _ChunkSet]
    ) -> _Prepared | RetrievalResult:
        """Product filter and query; a result already if the product has no chunks."""
        chunks, index = self._chunk_set(request, sets)
        if not chunks:
            return RetrievalResult(outcome=RetrievalOutcome.NO_PRODUCT_DOCUMENTS)
        query = build_query(
            request.text, self.vocabulary, known_terms=index.vocabulary, attribute=request.attribute
        )
        dense = self.params.fusion.dense_weight > 0 and any(c.embed_hash for c in chunks)
        return _Prepared(request, chunks, index, query, dense)

    def _finish(self, prepared: _Prepared, vector: list[float] | None) -> RetrievalResult:
        """Fuse, rerank and decide the outcome of one prepared search."""
        request, chunks, query = prepared.request, prepared.chunks, prepared.query
        fused = self._fuse(chunks, prepared.index, query, vector)
        if not fused:
            return RetrievalResult(
                outcome=RetrievalOutcome.NO_CANDIDATES,
                query=query,
                candidates_searched=len(chunks),
            )
        hits = self._rerank(query, fused, {c.id: c for c in chunks})
        outcome = (
            RetrievalOutcome.EVIDENCE
            if self._accepted(hits[0].rerank_score, hits[0].code_matches)
            else RetrievalOutcome.BELOW_THRESHOLD
        )
        logger.info(
            "retrieval done",
            extra={
                "manufacturer": request.manufacturer,
                "product_type": request.product_type,
                "outcome": outcome.value,
                "top_score": hits[0].rerank_score,
                "code_matches": list(hits[0].code_matches),
            },
        )
        return RetrievalResult(
            outcome=outcome, query=query, hits=tuple(hits), candidates_searched=len(chunks)
        )

    def _fuse(
        self,
        chunks: list[Chunk],
        index: Bm25Index,
        query: SearchQuery,
        vector: list[float] | None,
    ) -> list[FusedCandidate]:
        """BM25 and dense rankings of the filtered chunks, fused (dense only with a vector)."""
        limit = self.params.candidates
        terms = [*query.terms, *query.expansion_terms]
        lexical = [chunk_id for chunk_id, _ in index.rank(terms, limit)]
        dense: list[uuid.UUID] = []
        pairs = [(c.id, c.embed_hash) for c in chunks]
        hashes = {h for _, h in pairs if h is not None}
        if vector is not None and hashes:
            dense = dense_ranking(pairs, self._distances(vector, hashes), limit)
        return rrf_fuse(lexical, dense, self.params.fusion)

    def _distances(self, vector: list[float], hashes: set[str]) -> dict[str, float]:
        """Cosine distances of the filtered texts, exact or through the HNSW index."""
        model = self.embedder.model_name
        if self.params.dense_search == DenseSearch.HNSW:
            return self.embeddings.nearest_hnsw(
                model, vector, hashes, self.params.candidates, self.params.hnsw_ef_search
            )
        return self.embeddings.distances_to(model, vector, hashes)

    def evidence_hits(self, result: RetrievalResult) -> tuple[RetrievalHit, ...]:
        """The hits of a result that count as evidence, best first.

        Args:
            result: A retrieval result.

        Returns:
            Accepted hits only; none unless the outcome is EVIDENCE.
        """
        if result.outcome is not RetrievalOutcome.EVIDENCE:
            return ()
        return tuple(h for h in result.hits if self._accepted(h.rerank_score, h.code_matches))

    def _accepted(self, score: float, code_matches: tuple[str, ...]) -> bool:
        """A hit counts as evidence by score or by an exact requirement code."""
        return score >= self.params.min_score or bool(code_matches)

    def _rerank(
        self, query: SearchQuery, fused: list[FusedCandidate], by_id: dict[uuid.UUID, Chunk]
    ) -> list[RetrievalHit]:
        """Rerank every fused candidate; keep the top-k, accepted first, then by score."""
        passages = [embedding_text(by_id[c.chunk_id].raw_text) for c in fused]
        scores = self.reranker.score(query.rerank_text, passages)
        matches = [code_matches(query, by_id[c.chunk_id]) for c in fused]
        order = sorted(
            range(len(fused)),
            key=lambda i: (not self._accepted(scores[i], matches[i]), -scores[i], i),
        )[: self.params.top_k]
        return [
            RetrievalHit(
                chunk_id=fused[i].chunk_id,
                document_id=by_id[fused[i].chunk_id].document_id,
                page_number=by_id[fused[i].chunk_id].page_number,
                rerank_score=scores[i],
                code_matches=matches[i],
                rrf_score=fused[i].rrf_score,
                bm25_rank=fused[i].bm25_rank,
                dense_rank=fused[i].dense_rank,
            )
            for i in order
        ]


def code_matches(query: SearchQuery, chunk: Chunk) -> tuple[str, ...]:
    """The query's code terms that appear as whole tokens in the chunk's `search_text`.

    Args:
        query: The built query.
        chunk: A candidate chunk.

    Returns:
        Matched codes, in query order.
    """
    tokens = set(chunk.search_text.split())
    return tuple(t for t in query.code_terms if t in tokens)
