"""Tender line items and the atomic requirements split from them (fields mirror the ORM)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import Operator, RequirementType
from app.schemas.sections import EvidenceSource, SectionKind


class RequirementRules(BaseModel):
    """The requirement rules file (`config/requirement_rules.yaml`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind_types: dict[SectionKind, RequirementType]
    unclassified_type: RequirementType
    item_default_category: str
    vague: list[str] = Field(default_factory=list)
    mandatory: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    or_equivalent: list[str] = Field(default_factory=list)
    make_patterns: list[str] = Field(default_factory=list)
    single_firm_patterns: list[str] = Field(default_factory=list)
    make_list_pattern: str
    make_list_item_pattern: str
    make_list_end_pattern: str
    sentence_abbreviations: list[str] = Field(default_factory=list)
    item_start_pattern: str
    item_group_pattern: str
    item_qualifier_pattern: str
    item_clause_split_pattern: str
    segment_end_pattern: str
    segment_cut_pattern: str
    item_title_suffix_pattern: str = ""
    name_heading_follow_pattern: str = ""
    name_heading_max_lead_words: int = Field(default=0, ge=0)
    set_line_pattern: str = ""
    set_size_pattern: str = ""
    set_unreadable_pattern: str = ""
    name_ocr_confusions: dict[str, str] = Field(default_factory=dict)
    set_size_label: str = "Size"
    set_item_label: str = "Set contents"


class ValueKind(StrEnum):
    """How a requirement's value can be checked by rules."""

    NUMERIC = "numeric"  # a number with a unit or designation ("250 MM", "CLASS #150")
    CODE = "code"  # standards, grades and named options ("API 609", "ASTM A216 Gr. WCB")
    TEXT = "text"  # wording no rule can check ("HAND GEAR OPERATED")


class LineItem(BaseModel):
    """One item of a tender's schedule (BOQ / buyer specification row)."""

    model_config = ConfigDict(frozen=True)

    item_no: str
    title: str
    description_lines: list[str] = Field(description="Description cell, one printed line each.")
    description_header: str = Field(
        default="", description="Header of the description column; it can name a make (T9)."
    )
    quantity: str | None = None
    unit: str | None = None
    document: str = Field(description="Tender file the item comes from.")
    page_number: int = Field(ge=1)


class Requirement(BaseModel):
    """One atomic requirement of a line item (CLAUDE.md section 6, tender documents)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description='Stable within the tender: "<item>.<n>" or "<item>.<n>.<m>".')
    parent_id: str | None = None
    item_no: str
    position: int = Field(ge=0, description="Order within the item.")
    label: str | None = Field(default=None, description='Label as printed ("VALVE BODY MOC").')
    attribute: str | None = Field(default=None, description="Ontology attribute, if one applies.")
    req_type: RequirementType = RequirementType.TECHNICAL
    category: str | None = Field(default=None, description="Section category of the line.")
    evidence: EvidenceSource | None = Field(
        default=EvidenceSource.CATALOGUE,
        description="Where a vendor's evidence comes from; None if the category is unknown.",
    )
    product_type: str | None = Field(default=None, description="Item's ontology product type.")
    mandatory: bool | None = Field(default=None, description="None if the line does not say.")
    named_make: str | None = Field(default=None, description="Make named by the tender (D4).")
    single_firm: bool = Field(
        default=False, description="This line says only one firm is acceptable."
    )
    acceptable_makes: list[str] = Field(
        default_factory=list, description="Makes of an acceptable-make list in this line."
    )
    or_equivalent: bool = Field(default=False, description='The tender accepts "or equivalent".')
    flags: list[str] = Field(default_factory=list, description="Consistency warnings.")
    kind: ValueKind
    operator: Operator
    value_text: str = Field(description="The value as printed.")
    value_num: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = Field(
        default=None, description="Unit as printed (folded), or a designation."
    )
    canonical_unit: str | None = Field(
        default=None, description="pint unit after ontology lookup; None without an ontology."
    )
    alternatives: list[str] = Field(
        default_factory=list, description='Options separated by "/" ("CA15", "CF8M") or "or".'
    )
    any_alternative: bool = Field(
        default=False,
        description='Options written with "or" or "/": finding one of them is enough.',
    )
    raw_text: str = Field(description="Verbatim printed line(s); quotes come from here.")
    document: str
    page_number: int = Field(ge=1)
