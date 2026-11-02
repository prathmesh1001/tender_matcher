"""Queries for cached LLM judge answers."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models.judge_answer import JudgeAnswer
from app.db.repositories.base import SqlRepository


class JudgeAnswerRepository(SqlRepository[JudgeAnswer]):
    """Judge verdicts by cache key."""

    model = JudgeAnswer

    def verdict(self, cache_key: str) -> dict[str, Any] | None:
        """Return the stored verdict for a key, or None if the question was never answered."""
        stmt = select(JudgeAnswer.verdict).where(JudgeAnswer.cache_key == cache_key)
        return self._session.scalar(stmt)

    def store(
        self, cache_key: str, model: str, prompt_version: str, verdict: dict[str, Any]
    ) -> None:
        """Store a verdict; a key already stored (a parallel run) is left as it is.

        Args:
            cache_key: Hash of prompt version, model and prompt.
            model: Model that answered.
            prompt_version: Version of the judge prompt.
            verdict: The validated verdict as JSON.
        """
        stmt = (
            insert(JudgeAnswer)
            .values(
                cache_key=cache_key, model=model, prompt_version=prompt_version, verdict=verdict
            )
            .on_conflict_do_nothing(index_elements=[JudgeAnswer.cache_key])
        )
        self._session.execute(stmt)
        self._session.flush()
