"""Typed shape of an ontology file (`backend/ontology/<category>.yaml`)."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Dimension = Literal["none", "length", "pressure", "temperature", "flow", "power", "speed"]


class ProductType(BaseModel):
    """A kind of product, e.g. `butterfly_valve`, with the words tenders use for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    synonyms: list[str] = Field(min_length=1)
    sub_types: list[str] = Field(default_factory=list)


class Attribute(BaseModel):
    """A canonical attribute; numeric ones carry the unit every value is converted to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    dimension: Dimension
    canonical_unit: str | None = None
    synonyms: list[str] = Field(min_length=1)
    values: list[str] = Field(default_factory=list, description="Allowed enum values, if any.")


class CodeRule(BaseModel):
    r"""A regex over lower-cased text and the canonical code it is rewritten to (may use \1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str
    canonical: str = Field(min_length=1)

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        """Reject patterns that are not valid regular expressions."""
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex {value!r}: {exc}") from exc
        return value


class Ontology(BaseModel):
    """Everything known about one product category."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(pattern=r"^[a-z0-9_]+$")
    version: int = Field(ge=1)
    product_types: list[ProductType] = Field(min_length=1)
    attributes: list[Attribute] = Field(min_length=1)
    codes: list[CodeRule] = Field(default_factory=list)
    abbreviations: dict[str, str] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict, description="Spelling -> pint unit.")
    nps_to_dn: dict[str, int] = Field(default_factory=dict)
    class_to_pn: dict[int, int] = Field(default_factory=dict)

    def attribute(self, name: str) -> Attribute | None:
        """Return the attribute with this canonical name, or None."""
        return next((a for a in self.attributes if a.name == name), None)
