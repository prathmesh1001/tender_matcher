"""Typed shape of an ontology file (`backend/ontology/<category>.yaml`)."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Dimension = Literal["none", "length", "pressure", "temperature", "flow", "power", "speed"]
# What a catalogue value of the attribute means: an exact value, or a limit the product reaches
# ("max": a rating; a tender value at or below it is met, e.g. 10 bar against "16 bar").
Limit = Literal["exact", "max", "min"]


class SubType(BaseModel):
    """A design variant of a product type, e.g. a `triple_offset` butterfly valve.

    B2: a sub-type mismatch weakens a match; it is not a different product.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    synonyms: list[str] = Field(min_length=1)


class ProductType(BaseModel):
    """A kind of product, e.g. `butterfly_valve`, with the words documents use for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    synonyms: list[str] = Field(min_length=1)
    sub_types: list[SubType] = Field(default_factory=list)


class Attribute(BaseModel):
    """A canonical attribute; numeric ones carry the unit every value is converted to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    dimension: Dimension
    canonical_unit: str | None = None
    synonyms: list[str] = Field(min_length=1)
    values: list[str] = Field(default_factory=list, description="Allowed enum values, if any.")
    limit: Limit = Field(
        default="exact", description="Catalogue value is exact, a maximum or a minimum."
    )
    nominal_pipe_size: bool = Field(
        default=False, description="Sizes in inch map to DN through `nps_to_dn`, not by pint."
    )


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


class EquivalentGroup(BaseModel):
    """Names and grade codes that mean the same thing, e.g. cast iron = "CI" = "ASTM A126".

    A query naming any term also searches for every other term (catalogues often give only
    the grade code). Bare numbers are not allowed: "260" would match any "260 mm".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    terms: list[str] = Field(min_length=2)

    @field_validator("terms")
    @classmethod
    def _no_bare_numbers(cls, value: list[str]) -> list[str]:
        """Reject terms with a token that is only digits."""
        bad = [t for t in value if any(tok.isdigit() for tok in re.split(r"[\s\-/]+", t))]
        if bad:
            raise ValueError(f"terms must not contain bare numbers: {bad}")
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
    equivalents: list[EquivalentGroup] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict, description="Spelling -> pint unit.")
    prefix_units: dict[str, str] = Field(
        default_factory=dict, description='Units written before the number ("DN 50") -> pint.'
    )
    nps_to_dn: dict[str, int] = Field(default_factory=dict)
    class_to_pn: dict[int, int] = Field(default_factory=dict)

    def product_type(self, name: str) -> ProductType | None:
        """Return the product type with this canonical name, or None."""
        return next((p for p in self.product_types if p.name == name), None)

    def attribute(self, name: str) -> Attribute | None:
        """Return the attribute with this canonical name, or None."""
        return next((a for a in self.attributes if a.name == name), None)
