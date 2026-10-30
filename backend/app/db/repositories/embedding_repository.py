"""Queries for dense vectors (pgvector)."""

from collections.abc import Collection, Sequence
from typing import Final

from sqlalchemy import func, select

from app.db.models.embedding import TextEmbedding
from app.db.repositories.base import SqlRepository

# Session settings for one filtered HNSW query (iterative scan needs pgvector >= 0.8).
_HNSW_SETTINGS: Final = (("enable_seqscan", "off"), ("hnsw.iterative_scan", "relaxed_order"))


class EmbeddingRepository(SqlRepository[TextEmbedding]):
    """One vector per (model, text hash)."""

    model = TextEmbedding

    def existing_hashes(self, model_name: str, hashes: Collection[str]) -> set[str]:
        """Return which of `hashes` already have a vector for this model.

        Args:
            model_name: Embedding model identifier.
            hashes: Text hashes to look up.

        Returns:
            The subset that is already stored.
        """
        if not hashes:
            return set()
        stmt = select(TextEmbedding.text_hash).where(
            TextEmbedding.model == model_name, TextEmbedding.text_hash.in_(list(hashes))
        )
        return set(self._session.scalars(stmt))

    def distances_to(
        self, model_name: str, vector: Sequence[float], hashes: Collection[str]
    ) -> dict[str, float]:
        """Cosine distance from `vector` to each given text's vector (exact, no index).

        Used on the product-filtered chunks. There is deliberately no ORDER BY ... LIMIT: that
        would let Postgres pick the approximate HNSW index, which ranks the whole table first
        and can return fewer rows than exist inside the filter.

        Args:
            model_name: Only vectors of this model are compared.
            vector: Query vector (unit length).
            hashes: Text hashes to score.

        Returns:
            `text_hash -> cosine_distance` for the hashes that have a vector.
        """
        if not hashes:
            return {}
        distance = TextEmbedding.embedding.cosine_distance(list(vector))
        stmt = select(TextEmbedding.text_hash, distance).where(
            TextEmbedding.model == model_name, TextEmbedding.text_hash.in_(list(hashes))
        )
        return {h: float(d) for h, d in self._session.execute(stmt)}

    def nearest_hnsw(
        self,
        model_name: str,
        vector: Sequence[float],
        hashes: Collection[str],
        limit: int,
        ef_search: int,
    ) -> dict[str, float]:
        """Nearest of the given texts through the HNSW index (approximate, filtered in-index).

        pgvector's iterative scan keeps walking the graph until `limit` rows pass the filter.
        The planner prefers a plain scan on small tables, so for this one query sequential scans
        are disabled and the filter columns are wrapped (`concat(col, '')`) so no B-tree can
        serve them. The settings live in a savepoint that is rolled back, so nothing leaks.

        Args:
            model_name: Only vectors of this model are compared.
            vector: Query vector (unit length).
            hashes: Text hashes allowed in the result.
            limit: Number of results.
            ef_search: HNSW candidate list size.

        Returns:
            `text_hash -> cosine_distance` for up to `limit` nearest hashes.
        """
        if not hashes:
            return {}
        savepoint = self._session.begin_nested()  # SET LOCAL is undone with the savepoint
        try:
            for name, value in (*_HNSW_SETTINGS, ("hnsw.ef_search", str(ef_search))):
                self._session.execute(select(func.set_config(name, value, True)))
            distance = TextEmbedding.embedding.cosine_distance(list(vector))
            stmt = (
                select(TextEmbedding.text_hash, distance)
                .where(
                    func.concat(TextEmbedding.model, "") == model_name,
                    func.concat(TextEmbedding.text_hash, "").in_(list(hashes)),
                )
                .order_by(distance)
                .limit(limit)
            )
            return {h: float(d) for h, d in self._session.execute(stmt)}
        finally:
            savepoint.rollback()
