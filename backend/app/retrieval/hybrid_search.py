"""Fuse the BM25 and dense rankings of the product-filtered chunks (weighted RRF).

score(chunk) = w_bm25 / (k + rank_bm25) + w_dense / (k + rank_dense), ranks starting at 1; a
retriever that missed the chunk adds nothing. BM25 is weighted first (CLAUDE.md section 7):
exact codes ("ss316", "pn16") are what dense models blur (B3). Numbers inside ranges are not
a retrieval concern (B4): the comparator checks them on structured facts.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.schemas.retrieval import FusedCandidate


@dataclass(frozen=True, slots=True)
class FusionParams:
    """RRF constant and per-retriever weights (from config)."""

    k: int
    bm25_weight: float
    dense_weight: float


def dense_ranking(
    chunks: Sequence[tuple[uuid.UUID, str | None]],
    distances: Mapping[str, float],
    limit: int,
) -> list[uuid.UUID]:
    """Chunk ids ranked by cosine distance of their text's vector to the query vector.

    Args:
        chunks: `(chunk_id, embed_hash)` in a stable order; chunks without a vector are skipped.
        distances: `embed_hash -> cosine distance`.
        limit: Candidates to keep.

    Returns:
        Chunk ids, nearest first; ties keep chunk order.
    """
    scored = [
        (distances[h], i, chunk_id)
        for i, (chunk_id, h) in enumerate(chunks)
        if h is not None and h in distances
    ]
    scored.sort(key=lambda item: (item[0], item[1]))
    return [chunk_id for _, _, chunk_id in scored[:limit]]


def rrf_fuse(
    bm25: Sequence[uuid.UUID], dense: Sequence[uuid.UUID], params: FusionParams
) -> list[FusedCandidate]:
    """Weighted reciprocal rank fusion of two rankings.

    Args:
        bm25: Chunk ids ranked by BM25.
        dense: Chunk ids ranked by dense similarity.
        params: Fusion constant and weights.

    Returns:
        Every chunk in either ranking, best fused score first; ties prefer the better BM25 rank,
        then the better dense rank.
    """
    bm25_rank = {chunk_id: r for r, chunk_id in enumerate(bm25, start=1)}
    dense_rank = {chunk_id: r for r, chunk_id in enumerate(dense, start=1)}
    fused: list[FusedCandidate] = []
    for chunk_id in dict.fromkeys([*bm25, *dense]):
        b, d = bm25_rank.get(chunk_id), dense_rank.get(chunk_id)
        score = (params.bm25_weight / (params.k + b) if b else 0.0) + (
            params.dense_weight / (params.k + d) if d else 0.0
        )
        fused.append(FusedCandidate(chunk_id=chunk_id, rrf_score=score, bm25_rank=b, dense_rank=d))
    missing = len(bm25) + len(dense) + 1
    fused.sort(key=lambda c: (-c.rrf_score, c.bm25_rank or missing, c.dense_rank or missing))
    return fused
