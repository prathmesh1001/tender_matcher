"""Section category rules (`config/section_categories.yaml`) and section drafts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SectionKind(StrEnum):
    """What sort of requirement a section category holds."""

    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    ELIGIBILITY = "eligibility"
    LEGAL = "legal"
    ADMINISTRATIVE = "administrative"


class EvidenceSource(StrEnum):
    """Where a vendor's evidence for a category comes from."""

    CATALOGUE = "catalogue"
    VENDOR_DOCUMENTS = "vendor_documents"  # quotations, financials, certificates: not ingested
    NONE = "none"  # nothing to compare (buyer details, bid schedule)


class CategorySource(StrEnum):
    """What decided a section's or chunk's category."""

    HEADING = "heading"
    TEXT = "text"
    CHUNKS = "chunks"  # a section without a matching heading: majority of its chunks
    DEFAULT = "default"


class SectionCategory(BaseModel):
    """One category with the phrases that identify it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: SectionKind
    evidence: EvidenceSource
    description: str = ""
    headings: list[str] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)
    vendor_categories: list[str] = Field(default_factory=list)


class SectionRules(BaseModel):
    """The whole rules file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default: str
    categories: list[SectionCategory] = Field(min_length=1)

    @model_validator(mode="after")
    def _names_are_known(self) -> SectionRules:
        """Category names are unique; `default` and every `vendor_categories` entry exist."""
        names = [c.name for c in self.categories]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate category names in {names}")
        known = {*names, self.default}
        unknown = {v for c in self.categories for v in c.vendor_categories} - known
        if unknown:
            raise ValueError(f"unknown vendor_categories: {sorted(unknown)}")
        return self


class ChunkCategory(BaseModel):
    """Category of one chunk and what decided it."""

    model_config = ConfigDict(frozen=True)

    category: str
    source: CategorySource


class SectionDraft(BaseModel):
    """A run of consecutive chunks under one heading, ready to be stored."""

    model_config = ConfigDict(frozen=True)

    position: int = Field(ge=0, description="Order of the section in the document.")
    title: str | None
    number: str | None = Field(default=None, description='Heading number, e.g. "10.1".')
    parent_position: int | None = None
    category: str
    category_source: CategorySource
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    chunk_indexes: list[int] = Field(description="Indexes of its chunks, in reading order.")
