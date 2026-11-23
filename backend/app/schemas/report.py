"""The match report: per item, per manufacturer results with evidence, scores and ranking."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import (
    ExtractionIssueCode,
    ExtractionStatus,
    MatchStatus,
    VendorMatchLevel,
    VendorSource,
)
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
    seller_declared: bool = Field(
        default=False, description="A GeM listing: specifications typed in by the seller."
    )
    critical_met: float = 0.0
    critical_failed: int = 0
    product_heading: str | None = None
    other_product_passages: frozenset[tuple[int, str]] = Field(
        default_factory=frozenset,
        exclude=True,
        description="(page, raw_text) of chunks under another product's heading in the "
        "document; the LLM judge never reads them (B10). Not part of the report.",
    )
    fit_score: float
    decided: int


class TiedDocumentRows(BaseModel):
    """One tied catalogue's own result for every requirement, with its evidence."""

    model_config = ConfigDict(frozen=True)

    document: str
    results: list[Comparison]


class ManufacturerItemReport(BaseModel):
    """One manufacturer's results for one item, from its chosen catalogue only."""

    model_config = ConfigDict(frozen=True)

    manufacturer: str
    chosen: CandidateSummary | None = Field(
        description="The catalogue every result comes from; None if none declares the type."
    )
    candidates: list[CandidateSummary]
    tied_documents: list[str] = Field(
        default_factory=list,
        description="Catalogues nothing tells apart: none is chosen, a reader must decide.",
    )
    tied_rows: list[TiedDocumentRows] = Field(
        default_factory=list, description="Each tied catalogue's rows and evidence."
    )
    results: list[Comparison] = Field(
        description="One per atomic requirement, in order. On a tie these only carry the score "
        "the tied catalogues share, all flagged; read `tied_rows` for what each one says."
    )
    score: Score


class GemRecommendation(BaseModel):
    """A GeM listing in a category the buyer selected: offered, not checked against the spec."""

    model_config = ConfigDict(frozen=True)

    brand: str
    product: str = Field(description="The listing's title on GeM.")
    url: str
    category: str = Field(description="The buyer's GeM category the listing is in.")
    sold_as: str | None = Field(default=None, description='"oem", "resellers authorized", ...')
    reason: str = Field(default="", description="Why it is shown for this item, if for one.")


class ItemVendor(BaseModel):
    """One of the best vendors for one item, with its score on that item alone."""

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    manufacturer: str
    score: Score
    low_coverage: bool = Field(
        description="Coverage on this item is below the configured minimum; listed after others."
    )
    source: VendorSource
    document: str = Field(description="Title of the document the score rests on.")
    source_url: str | None = Field(
        default=None, description="The GeM listing page, for a GeM vendor."
    )
    match_level: VendorMatchLevel | None = Field(
        default=None, description="Category from the rules (`scorer.vendor_level`)."
    )
    level_reason: str = Field(
        default="",
        description="Why it needs review, or what a partial match by product name rests on.",
    )


class TenderVendor(BaseModel):
    """One of the tender's best vendors, over all its items (`reporting.best_vendors`)."""

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    name: str = Field(description="Manufacturer (catalogue) or GeM brand.")
    source: VendorSource | None = Field(
        description="Where its evidence comes from; None for a buyer-category GeM seller that "
        "no item was checked against (related only)."
    )
    match_level: VendorMatchLevel | None = Field(
        description="Its best level on any item; None when related only (never checked)."
    )
    level_reason: str = Field(default="", description="Why that level, in words, if needed.")
    items_full: int = Field(default=0, ge=0, description="Items it fully matches.")
    items_partial: int = Field(default=0, ge=0, description="Items it partly matches.")
    items_listed: int = Field(
        default=0, ge=0, description="Items it is among the top vendors of, at any level."
    )
    best_compatibility_pct: float | None = Field(
        default=None, description="Its highest compatibility % on one item."
    )
    source_url: str | None = Field(default=None, description="A GeM listing page, if any.")
    category: str | None = Field(
        default=None, description="The buyer's GeM category, for a related seller."
    )


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
    top_vendors: list[ItemVendor] = Field(
        default_factory=list,
        description="The best vendors for this item alone (catalogue makers and GeM sellers), "
        "ranked like the tender ranking; only vendors with a document and a decision.",
    )
    related_gem_vendors: list[GemRecommendation] = Field(
        default_factory=list,
        description="When no GeM vendor matched the item: listings of a buyer category sharing "
        "a product word with the item title. Related only, never checked against the spec.",
    )


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


class ExtractionIssue(BaseModel):
    """One reason a tender's extraction cannot be trusted."""

    model_config = ConfigDict(frozen=True)

    code: ExtractionIssueCode
    reason: str


class ExtractionQuality(BaseModel):
    """Whether the items of this tender were read correctly. The reader must see this first."""

    model_config = ConfigDict(frozen=True)

    status: ExtractionStatus = ExtractionStatus.OK
    reasons: list[ExtractionIssue] = Field(default_factory=list)


class StatusListEntry(BaseModel):
    """One requirement in a status list."""

    model_config = ConfigDict(frozen=True)

    item_no: str
    requirement_id: str
    requirement: str = Field(description="The requirement as printed.")
    needs_review: bool


class ManufacturerStatusList(BaseModel):
    """Every requirement of one manufacturer, listed under its status."""

    model_config = ConfigDict(frozen=True)

    manufacturer: str
    lists: dict[MatchStatus, list[StatusListEntry]]


class SparePartsNotice(BaseModel):
    """The tender buys spare parts: catalogues and GeM rarely list them as products."""

    model_config = ConfigDict(frozen=True)

    boq_title: str = Field(description="The bid's BOQ title that names the spare parts.")
    oem: str | None = Field(
        default=None,
        description="The maker the BOQ title names, if it is a manufacturer in the database; "
        "spare parts usually come from that maker (original spares).",
    )


class TenderReport(BaseModel):
    """Everything one match run produced for one tender."""

    model_config = ConfigDict(frozen=True)

    tender: str = Field(description="Tender folder name (bid number with underscores).")
    bid_number: str
    tender_revision: int | None = Field(
        default=None,
        description="Which revision of bid.pdf this run used; None outside a stored run.",
    )
    bid_sha256: str | None = Field(default=None, description="That bid.pdf's content hash.")
    extraction_quality: ExtractionQuality = Field(
        default_factory=ExtractionQuality,
        description="If unreliable, every result of this tender is marked needs_review.",
    )
    single_firm: SingleFirmWarning | None = Field(
        default=None,
        description="Set if only one firm is acceptable; separate from `named_makes`.",
    )
    tender_files: list[str]
    generated_at: datetime
    ontology_used: bool
    decided_by: str = Field(default="rules", description="Rules, plus the LLM model if it ran.")
    items: list[ItemReport]
    ranking: list[RankingEntry]
    score_explanation: str = Field(default="", description="How the % are worked out, in words.")
    status_scores: dict[MatchStatus, float] = Field(
        default_factory=dict, description="Score of each decided status."
    )
    status_lists: list[ManufacturerStatusList] = Field(
        default_factory=list, description="Requirements under each status, per manufacturer."
    )
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
    boq_title: str | None = Field(default=None, description="The bid's BOQ title, if printed.")
    buyer_gem_categories: list[str] = Field(
        default_factory=list,
        description="GeM categories the buyer selected for notification; searched first on GeM.",
    )
    spare_parts: SparePartsNotice | None = Field(
        default=None, description="Set when the BOQ title buys spare parts."
    )
    recommended_gem_vendors: list[GemRecommendation] = Field(
        default_factory=list,
        description="Listings in the GeM categories the buyer selected for the bid; the tender's "
        "recommended GeM vendors even where no item matched one.",
    )
    best_vendors: list[TenderVendor] = Field(
        default_factory=list,
        description="The tender's best vendors over all items (database and GeM): full and "
        "partial matches first, filled up to a minimum with the next best under their own level.",
    )
