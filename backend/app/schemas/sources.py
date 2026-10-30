"""Provenance records for manufacturer documents (`data/manufacturers/<mfr>/sources.yaml`)."""

from datetime import date, datetime
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl

from app.schemas.enums import DocType

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SourceEntry(BaseModel):
    """One document as written in `sources.yaml` (manufacturer comes from the file header)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(min_length=1, description="PDF filename, relative to the manufacturer dir.")
    category: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    product_types: list[str] = Field(
        min_length=1, description="Product types this document covers (ontology names)."
    )
    sub_types: list[str] = Field(
        default_factory=list, description="Sub-types it is limited to, if any."
    )
    title: str = Field(min_length=1)
    doc_type: DocType
    url: HttpUrl = Field(description="Public URL the document was downloaded from.")
    fetched_at: AwareDatetime = Field(description="When it was downloaded (with timezone).")
    publish_date: date | None = Field(default=None, description="Date printed on the document.")
    sha256: str = Field(pattern=SHA256_PATTERN, description="Lower-case hex SHA-256 of the PDF.")
    notes: str | None = None


class SourcesFile(BaseModel):
    """Top-level shape of a `sources.yaml` file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manufacturer: str = Field(min_length=1)
    documents: list[SourceEntry] = Field(min_length=1)


class RegisteredSource(BaseModel):
    """A validated source entry with its manufacturer and resolved on-disk path."""

    model_config = ConfigDict(frozen=True)

    manufacturer: str
    category: str
    product_types: list[str]
    sub_types: list[str]
    title: str
    doc_type: DocType
    url: HttpUrl
    fetched_at: datetime
    publish_date: date | None
    sha256: str
    path: Path
    registry_file: Path = Field(description="The sources.yaml this entry was read from.")
