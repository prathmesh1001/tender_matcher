"""Spec facts: what the LLM proposes, and what is kept after verification."""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExtractedFact(BaseModel):
    """One fact as the LLM proposes it. Nothing here is trusted until verified."""

    model_config = ConfigDict(frozen=True)

    chunk: int = Field(description="Index of the chunk the fact comes from.")
    attribute: str = Field(description="One of the listed canonical attribute names.")
    label: str = Field(description="The attribute's label, copied exactly from the chunk.")
    value: str = Field(description="The value, copied exactly from the chunk.")
    model: str | None = Field(
        default=None, description="Model or variant the value applies to, if the chunk names one."
    )


class ExtractedFacts(BaseModel):
    """The LLM's answer for one batch of chunks."""

    model_config = ConfigDict(frozen=True)

    facts: list[ExtractedFact]


class DropReason(StrEnum):
    """Why a proposed fact was not kept."""

    UNKNOWN_CHUNK = "unknown_chunk"
    UNKNOWN_ATTRIBUTE = "unknown_attribute"
    LABEL_MISMATCH = "label_mismatch"  # the label does not name the attribute (like-for-like)
    NOT_IN_CHUNK = "not_in_chunk"
    QUOTE_NOT_ON_PAGE = "quote_not_on_page"
    NO_COMPARABLE_NUMBER = "no_comparable_number"


class SpecFactDraft(BaseModel):
    """A verified fact, ready to store. Page, numbers and unit never come from the LLM."""

    model_config = ConfigDict(frozen=True)

    chunk_id: uuid.UUID | None
    page_number: int = Field(ge=1)
    product_family: str | None
    model: str | None
    attribute: str
    value_text: str
    value_num: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    unit: str | None = None
    quote: str = Field(description='Verified pieces as printed, joined by " | ".')
    quote_match_score: float
    ocr: bool = False
    conflict: bool = False
