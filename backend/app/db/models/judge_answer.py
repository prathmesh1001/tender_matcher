"""LLM judge answers, kept so a rerun asks nothing twice and gives the same result."""

from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class JudgeAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One verdict, keyed by a hash of the prompt version, the model and the full prompt.

    The prompt holds the requirement text and the cited passages with their chunk ids, so any
    change to them, to the prompt or to the model gives a new key and a fresh answer.
    """

    __tablename__ = "judge_answers"

    cache_key: Mapped[str] = mapped_column(
        String(64), unique=True, comment="sha256 of prompt version, model and prompt."
    )
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(16))
    verdict: Mapped[dict[str, Any]] = mapped_column(comment="The validated JudgeVerdict as JSON.")
