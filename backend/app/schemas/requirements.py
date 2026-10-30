"""Tender line items and the atomic requirements split from them (fields mirror the ORM)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import Operator, RequirementType


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
        default_factory=list, description='Options separated by "/" ("CA15", "CF8M").'
    )
    raw_text: str = Field(description="Verbatim printed line(s); quotes come from here.")
    document: str
    page_number: int = Field(ge=1)
