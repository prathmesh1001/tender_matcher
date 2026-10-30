"""Weighted RRF fusion, dense ranking, and the query-variant set (CLAUDE.md section 12)."""

import uuid

import pytest

from app.retrieval.bm25_index import Bm25Index, Bm25Params
from app.retrieval.hybrid_search import FusionParams, dense_ranking, rrf_fuse
from app.retrieval.query_builder import QueryVocabulary, build_query
from app.retrieval.text_normalizer import normalize_for_search

FUSION = FusionParams(k=60, bm25_weight=1.0, dense_weight=0.7)
A, B, C, D = (uuid.uuid4() for _ in range(4))


def test_rrf_scores_and_order() -> None:
    fused = rrf_fuse([A, B], [B, C], FUSION)
    assert [c.chunk_id for c in fused] == [B, A, C]
    b = fused[0]
    assert b.rrf_score == pytest.approx(1.0 / 62 + 0.7 / 61)
    assert (b.bm25_rank, b.dense_rank) == (2, 1)
    assert fused[2].bm25_rank is None


def test_bm25_is_weighted_first() -> None:
    # Same rank in each list: the BM25 hit wins.
    fused = rrf_fuse([A], [B], FUSION)
    assert [c.chunk_id for c in fused] == [A, B]


def test_ties_prefer_the_better_bm25_rank() -> None:
    equal = FusionParams(k=60, bm25_weight=1.0, dense_weight=1.0)
    fused = rrf_fuse([A, B], [B, A], equal)
    assert fused[0].rrf_score == fused[1].rrf_score
    assert [c.chunk_id for c in fused] == [A, B]


def test_bm25_only_when_dense_is_empty() -> None:
    assert [c.chunk_id for c in rrf_fuse([A, B], [], FUSION)] == [A, B]
    assert rrf_fuse([], [], FUSION) == []


def test_dense_ranking_skips_chunks_without_vectors() -> None:
    chunks = [(A, "ha"), (B, None), (C, "hc"), (D, "ha")]
    ranked = dense_ranking(chunks, {"ha": 0.4, "hc": 0.1}, limit=10)
    assert ranked == [C, A, D]  # same text -> same distance, chunk order breaks the tie
    assert dense_ranking(chunks, {"ha": 0.4, "hc": 0.1}, limit=1) == [C]


CATALOGUE = [
    "Swing check valve PN16 flanged, body WCB",
    "Dual plate check valve PN25 wafer type",
    "Gland packing: graphite",
    "Non return valve hydro test 24 bar",
]


@pytest.mark.parametrize(
    "variants",
    [
        ["Check Valves PN 16 flanged", "check vlave PN16 flanged", "NRV PN-16 flanged"],
        ["Dual plate check valves wafer", "dual plate chek vlave wafer", "dual plate NRV wafer"],
    ],
)
def test_query_variants_return_the_same_top_chunk(
    query_vocab: QueryVocabulary, variants: list[str]
) -> None:
    # Section 12: the same query written >= 3 ways (plural, typo, abbreviation) -> same top-1.
    ids = [uuid.uuid4() for _ in CATALOGUE]
    pairs = [
        (i, normalize_for_search(t, query_vocab.search))
        for i, t in zip(ids, CATALOGUE, strict=True)
    ]
    index = Bm25Index.build(pairs, Bm25Params(k1=1.5, b=0.75))
    tops = set()
    for text in variants:
        query = build_query(text, query_vocab, known_terms=index.vocabulary)
        ranked = [c for c, _ in index.rank([*query.terms, *query.expansion_terms], 10)]
        tops.add(rrf_fuse(ranked, [], FUSION)[0].chunk_id)
    assert len(tops) == 1
