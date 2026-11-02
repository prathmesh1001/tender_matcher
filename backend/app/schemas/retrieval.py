"""Inputs and outputs of retrieval (query builder -> hybrid search -> rerank)."""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TypoCorrection(BaseModel):
    """A tender word replaced by the ontology word it was most likely meant to be (A2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    original: str
    corrected: str
    distance: int = Field(ge=1)


class SearchQuery(BaseModel):
    """A requirement turned into what the two retrievers search with.

    `terms` are `search_text` tokens (BM25): the requirement's own tokens first, then distinct
    expansion tokens (abbreviations, ontology synonyms). `dense_text` is the requirement as
    written plus corrected words and abbreviation expansions; it feeds the embedder.
    `rerank_text` is `dense_text` led by the attribute's name ("body material: Body- CI ..."):
    a bare spec fragment gives the cross-encoder too little to judge. `code_terms` are the
    requirement's codes plus the grade codes of materials it names ("en593", "a536"); bare
    numbers are never codes (B4: values inside ranges are the comparator's job).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_text: str
    terms: tuple[str, ...]
    expansion_terms: tuple[str, ...] = ()
    dense_text: str
    rerank_text: str
    code_terms: tuple[str, ...] = ()
    corrections: tuple[TypoCorrection, ...] = ()


class FusedCandidate(BaseModel):
    """A chunk after reciprocal rank fusion; a rank is None if that retriever missed it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: uuid.UUID
    rrf_score: float
    bm25_rank: int | None = Field(default=None, ge=1)
    dense_rank: int | None = Field(default=None, ge=1)


class RetrievalHit(BaseModel):
    """A reranked chunk returned as possible evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    page_number: int
    rerank_score: float = Field(ge=0, le=1)
    code_matches: tuple[str, ...] = Field(
        default=(), description="Requirement codes found in the chunk's search_text."
    )
    rrf_score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None


class RetrievalOutcome(StrEnum):
    """Why retrieval returned what it did; anything but EVIDENCE means `unsupported`."""

    EVIDENCE = "evidence"
    NO_PRODUCT_DOCUMENTS = "no_product_documents"  # manufacturer has nothing for the type
    NO_CANDIDATES = "no_candidates"  # neither retriever matched any chunk
    BELOW_THRESHOLD = "below_threshold"  # best score < RERANK_MIN_SCORE and no code match


class RetrievalRequest(BaseModel):
    """One requirement searched in one manufacturer's catalogues for one product type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manufacturer: str = Field(min_length=1)
    category: str = Field(min_length=1)
    product_type: str = Field(min_length=1)
    text: str = Field(min_length=1)
    attribute: str | None = Field(default=None, description="Canonical ontology attribute.")
    document_sha256: str | None = Field(
        default=None, description="Search only this catalogue; None for all of the manufacturer's."
    )


class DocumentIndexStatus(BaseModel):
    """How much of one stored document is searchable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_path: str
    chunks: int = Field(ge=0)
    with_vector: int = Field(ge=0)
    spec_facts: int = Field(ge=0)


class RetrievalResult(BaseModel):
    """Top reranked chunks (best first) and the outcome deciding whether they count."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: RetrievalOutcome
    query: SearchQuery | None = None
    hits: tuple[RetrievalHit, ...] = ()
    candidates_searched: int = Field(default=0, ge=0)
