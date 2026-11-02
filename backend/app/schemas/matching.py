"""Result of comparing one requirement with one manufacturer's catalogue chunks."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.schemas.enums import DecisionSource, MatchStatus, ReviewFlag


class LabelBasis(StrEnum):
    """How a catalogue field was judged to state the requirement's attribute."""

    ATTRIBUTE = "attribute"  # same ontology attribute on both sides
    LABEL_WORDS = "label_words"  # no ontology attribute: the label's key words appear
    NONE = "none"  # no field named the attribute


class Evidence(BaseModel):
    """One catalogue field cited for a decision. Text is verbatim from the chunk."""

    model_config = ConfigDict(frozen=True)

    document: str = Field(description="Catalogue file name.")
    sha256: str
    title: str
    page_number: int = Field(ge=1)
    label: str = Field(description="The field's label as read (row key + column, or line key).")
    value: str = Field(description="The field's value as printed.")
    quote: str = Field(description="Verbatim substring of the chunk's raw_text.")
    quote_parts: list[str] = Field(
        description="Verbatim pieces printed on the page (cells or lines), for verification."
    )
    status: MatchStatus | None = Field(
        default=None, description="What this field alone decides; None if it decides nothing."
    )
    reason: str = ""
    verified: bool | None = Field(
        default=None, description="Quote found on the cited page; None if not checked yet."
    )
    match_score: float | None = Field(
        default=None, description="Lowest fuzzy score of the quote's pieces on the page."
    )


class SubTypeFit(StrEnum):
    """How a catalogue's declared sub-types relate to the tender item's sub-type (B2)."""

    MATCH = "match"  # the document declares the item's sub-type
    GENERAL = "general"  # the document declares no sub-type for this product
    UNKNOWN = "unknown"  # the item names no sub-type
    MISMATCH = "mismatch"  # the document declares other sub-types only


class JudgeStatus(StrEnum):
    """What the LLM judge may answer (`not_applicable` is never its call)."""

    FULL_MATCH = "full_match"
    PARTIAL_MATCH = "partial_match"
    MISSING_CAPABILITY = "missing_capability"
    UNSUPPORTED = "unsupported"


class JudgeVerdict(BaseModel):
    """The LLM judge's answer for one requirement; never trusted until verified."""

    status: JudgeStatus
    capability: str
    chunk_id: str
    quote: str
    reasoning: str


class Comparison(BaseModel):
    """Decision for one requirement against one manufacturer."""

    model_config = ConfigDict(frozen=True)

    requirement_id: str
    status: MatchStatus
    needs_review: bool
    decided_by: DecisionSource
    basis: LabelBasis
    reason: str
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Fields behind the status; for unsupported, labelled fields that decided "
        "nothing (cited so a reviewer can look).",
    )
    flags: list[ReviewFlag] = Field(
        default_factory=list, description="Weaknesses seen while deciding; each lowers confidence."
    )
    confidence: float | None = Field(
        default=None, ge=0, le=1, description="How far the decision can be trusted; None if none."
    )
    confidence_factors: list[str] = Field(
        default_factory=list, description="What lowered the confidence, with each factor."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def capability(self) -> str | None:
        """What the catalogue states for this requirement: the field that decided it, as printed."""
        deciding = next((e for e in self.evidence if e.status is not None), None)
        return f"{deciding.label}: {deciding.value}" if deciding else None


class DocumentResult(BaseModel):
    """One catalogue document evaluated on its own for one tender item (never pooled)."""

    model_config = ConfigDict(frozen=True)

    document: str = Field(description="Catalogue file name.")
    sha256: str
    title: str
    declared_sub_types: list[str]
    sub_type_fit: SubTypeFit
    critical_met: float = Field(
        default=0.0, description="Sum of status scores over critical requirements."
    )
    critical_failed: int = Field(
        default=0, ge=0, description="Critical requirements the document states and fails."
    )
    fit_score: float = Field(description="Sum of status scores over decided requirements.")
    decided: int = Field(ge=0, description="Requirements with a rule decision.")
    product_heading: str | None = Field(
        default=None,
        description="The product heading chosen inside the document, if it holds several.",
    )
    comparisons: list[Comparison]


class ManufacturerItemResult(BaseModel):
    """All candidate documents of one manufacturer for one item, and the one chosen."""

    model_config = ConfigDict(frozen=True)

    manufacturer: str
    item_no: str
    product_type: str | None
    item_sub_type: str | None
    chosen: str | None = Field(
        default=None, description="sha256 of the chosen document; None if there is none."
    )
    documents: list[DocumentResult] = Field(
        default_factory=list, description="Candidates, best first."
    )
    tied_documents: list[str] = Field(
        default_factory=list, description="Catalogues nothing tells apart; none is then chosen."
    )
