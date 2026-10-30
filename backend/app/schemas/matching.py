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
