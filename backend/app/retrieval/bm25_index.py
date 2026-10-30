"""Okapi BM25 over chunk `search_text`, built per search on the product-filtered chunks.

The filtered set (one manufacturer, one product type) is small, so the index lives in memory
for one query and never goes stale. Each chunk is its own BM25 document, so a long catalogue
cannot outrank a short exact row by repeating "valve" (B8: length normalization via `b`).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi


@dataclass(frozen=True, slots=True)
class Bm25Params:
    """Okapi BM25 parameters (from config)."""

    k1: float
    b: float


@dataclass(frozen=True, slots=True)
class Bm25Index:
    """BM25 over a fixed list of chunks."""

    chunk_ids: tuple[uuid.UUID, ...]
    doc_terms: tuple[frozenset[str], ...] = field(repr=False)
    vocabulary: frozenset[str] = field(repr=False)
    _model: BM25Okapi | None = field(repr=False)

    @classmethod
    def build(cls, chunks: Sequence[tuple[uuid.UUID, str]], params: Bm25Params) -> Bm25Index:
        """Index chunks by their whitespace-separated `search_text` tokens.

        Args:
            chunks: `(chunk_id, search_text)` pairs.
            params: BM25 parameters.

        Returns:
            The index (empty if no chunk has any token).
        """
        corpus = [text.split() for _, text in chunks]
        doc_terms = tuple(frozenset(tokens) for tokens in corpus)
        vocabulary = frozenset().union(*doc_terms)
        # rank_bm25 divides by the corpus size and by its total length: both must be > 0.
        model = BM25Okapi(corpus, k1=params.k1, b=params.b) if vocabulary else None
        return cls(
            chunk_ids=tuple(chunk_id for chunk_id, _ in chunks),
            doc_terms=doc_terms,
            vocabulary=vocabulary,
            _model=model,
        )

    def rank(self, terms: Sequence[str], limit: int) -> list[tuple[uuid.UUID, float]]:
        """Chunks sharing at least one term with the query, best first.

        Presence decides membership, not the score sign: in a tiny corpus rank_bm25's IDF of a
        term found in most chunks can be <= 0.

        Args:
            terms: Query stems (repeats count, as in BM25).
            limit: Maximum number of results.

        Returns:
            `(chunk_id, score)` pairs; ties keep index order.
        """
        present = [t for t in terms if t in self.vocabulary]
        if self._model is None or not present:
            return []
        scores = self._model.get_scores(present)
        wanted = set(present)
        matched = [i for i, doc in enumerate(self.doc_terms) if doc & wanted]
        matched.sort(key=lambda i: (-float(scores[i]), i))
        return [(self.chunk_ids[i], float(scores[i])) for i in matched[:limit]]
