"""Result of comparing one requirement with one manufacturer's catalogue chunks."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import DecisionSource, MatchStatus


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


class DocumentResult(BaseModel):
    """One catalogue document evaluated on its own for one tender item (never pooled)."""

    model_config = ConfigDict(frozen=True)

    document: str = Field(description="Catalogue file name.")
    sha256: str
    title: str
    declared_sub_types: list[str]
    sub_type_fit: SubTypeFit
    fit_score: float = Field(description="Sum of status scores over decided requirements.")
    decided: int = Field(ge=0, description="Requirements with a rule decision.")
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
