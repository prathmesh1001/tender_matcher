"""The match report: per item, per manufacturer results with evidence, scores and ranking."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import MatchStatus
from app.schemas.matching import Comparison, SubTypeFit
from app.schemas.requirements import LineItem, Requirement


class Score(BaseModel):
    """Scores over a set of atomic requirements (CLAUDE.md section 7).

    compatibility_pct = Σ(score * weight) / Σ(weight) over decided requirements (full,
    partial, missing); unsupported ones are excluded. coverage_pct = decided / all.
    """

    model_config = ConfigDict(frozen=True)

    compatibility_pct: float | None = Field(description="None when nothing was decided.")
    coverage_pct: float
    decided: int = Field(ge=0)
    total: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    counts: dict[MatchStatus, int]


class CandidateSummary(BaseModel):
    """One catalogue considered for an item (all candidates are listed, best first)."""

    model_config = ConfigDict(frozen=True)

    document: str
    sha256: str
    title: str
    sub_type_fit: SubTypeFit
    critical_met: float = 0.0
    critical_failed: int = 0
    fit_score: float
    decided: int


class ManufacturerItemReport(BaseModel):
    """One manufacturer's results for one item, from its chosen catalogue only."""

    model_config = ConfigDict(frozen=True)

    manufacturer: str
    chosen: CandidateSummary | None = Field(
        description="The catalogue every result comes from; None if none declares the type."
    )
    candidates: list[CandidateSummary]
    results: list[Comparison] = Field(description="One per atomic requirement, in order.")
    score: Score


class ItemReport(BaseModel):
    """One tender line item and how each manufacturer meets it."""

    model_config = ConfigDict(frozen=True)

    item: LineItem
    category: str | None = Field(description="Ontology category used; None without one.")
    product_type: str | None
    sub_type: str | None = Field(
        default=None, description="Sub-type named by the title (as seen by the ontology)."
    )
    requirements: list[Requirement] = Field(description="All requirements, parents included.")
    manufacturers: list[ManufacturerItemReport]


class RankingEntry(BaseModel):
    """A manufacturer's tender-level score over all items."""

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    manufacturer: str
    score: Score
    items_with_catalogue: int = Field(ge=0)
    low_coverage: bool = Field(
        description="Coverage is below the configured minimum; listed after the others."
    )


class Citation(BaseModel):
    """A tender line behind a statement: file, page, words as printed."""

    model_config = ConfigDict(frozen=True)

    document: str
    page_number: int = Field(ge=1)
    quote: str


class SingleFirmWarning(BaseModel):
    """The tender says only one firm is acceptable. The reader must see this first."""

    model_config = ConfigDict(frozen=True)

    makes: list[str] = Field(description="The firm, if the tender says which.")
    citations: list[Citation]


class TenderReport(BaseModel):
    """Everything one match run produced for one tender."""

    model_config = ConfigDict(frozen=True)

    tender: str = Field(description="Tender folder name (bid number with underscores).")
    bid_number: str
    single_firm: SingleFirmWarning | None = Field(
        default=None,
        description="Set if only one firm is acceptable; separate from `named_makes`.",
    )
    tender_files: list[str]
    generated_at: datetime
    ontology_used: bool
    decided_by: str = Field(default="rules", description="Rules only: no LLM judge yet.")
    items: list[ItemReport]
    ranking: list[RankingEntry]
    general_requirements: list[Requirement] = Field(
        default_factory=list, description="Requirements outside the item schedule."
    )
    general_results: list[Comparison] = Field(
        default_factory=list,
        description="One per general requirement; these never enter scores or the ranking.",
    )
    item_source: str = Field(
        default="none",
        description="Where the items came from: item_list, spec_schedule, item_category, none.",
    )
    named_makes: list[str] = Field(
        default_factory=list, description="Makes the tender names; a reviewer must read these."
    )
