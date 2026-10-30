"""BM25 over chunk search_text."""

import uuid

from app.retrieval.bm25_index import Bm25Index, Bm25Params
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search

PARAMS = Bm25Params(k1=1.5, b=0.75)


def _index(texts: list[str], vocab: SearchVocabulary) -> tuple[Bm25Index, list[uuid.UUID]]:
    ids = [uuid.uuid4() for _ in texts]
    pairs = [(i, normalize_for_search(t, vocab)) for i, t in zip(ids, texts, strict=True)]
    return Bm25Index.build(pairs, PARAMS), ids


def test_exact_row_ranks_first(search_vocab: SearchVocabulary) -> None:
    index, ids = _index(["Body material SS 316", "Disc material CF8M", "Seat PTFE"], search_vocab)
    ranked = index.rank(normalize_for_search("body SS316", search_vocab).split(), limit=10)
    assert ranked[0][0] == ids[0]


def test_only_chunks_sharing_a_term_are_returned(search_vocab: SearchVocabulary) -> None:
    index, ids = _index(["gland packing graphite", "stem 17-4PH", "seat PTFE"], search_vocab)
    ranked = index.rank(["ptfe"], limit=10)
    assert [chunk_id for chunk_id, _ in ranked] == [ids[2]]


def test_term_in_most_chunks_still_returns_them(search_vocab: SearchVocabulary) -> None:
    # rank_bm25's IDF goes <= 0 for a term in most chunks; presence still decides membership.
    index, _ = _index(["valve a", "valve b", "valve c"], search_vocab)
    assert len(index.rank(["valv"], limit=10)) == 3


def test_long_chunk_repeating_a_word_does_not_win(search_vocab: SearchVocabulary) -> None:
    # B8: a long page saying "valve" over and over must not beat the short exact row.
    long_text = " ".join(["valve"] * 60 + ["general", "description"] * 40)
    index, ids = _index(["Ball valve full bore", long_text, "gate"], search_vocab)
    ranked = index.rank(normalize_for_search("ball valve", search_vocab).split(), limit=10)
    assert ranked[0][0] == ids[0]


def test_limit_and_unknown_terms(search_vocab: SearchVocabulary) -> None:
    index, _ = _index(["seat ptfe", "seat rptfe", "seat metal"], search_vocab)
    assert len(index.rank(["seat"], limit=2)) == 2
    assert index.rank(["unknown"], limit=5) == []


def test_empty_index() -> None:
    index = Bm25Index.build([], PARAMS)
    assert index.rank(["valv"], limit=5) == []
    assert Bm25Index.build([(uuid.uuid4(), "")], PARAMS).rank(["valv"], limit=5) == []
