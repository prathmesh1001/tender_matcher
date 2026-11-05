"""Tender file provenance (`data/tenders/<bid>/tender.yaml`) and link-fetching rules."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.sources import SHA256_PATTERN

LinkAction = Literal["fetch", "skip"]


class LinkRule(BaseModel):
    """One URL pattern and what to do with links that match it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(pattern=r"^[a-z0-9_]+$")
    pattern: str = Field(min_length=1)
    action: LinkAction
    purpose: str = Field(min_length=1)


class LinkRules(BaseModel):
    """Top-level shape of the link rules file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_action: LinkAction
    rules: list[LinkRule] = Field(min_length=1)


class ManifestFile(BaseModel):
    """One file entry of a tender manifest.

    `file` is None when the response was not stored (empty, or a duplicate of another file).
    `url` is None for user-provided copies whose source link is unknown.
    """

    model_config = ConfigDict(extra="forbid")

    file: str | None
    role: str = Field(min_length=1)
    url: str | None
    fetched_at: AwareDatetime | None = None
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    content_type: str | None = None
    found_in: str | None = None
    depth: int | None = Field(default=None, ge=0)
    duplicate_of: str | None = None
    note: str | None = None
    original_name: str | None = None


class TenderManifest(BaseModel):
    """Top-level shape of `tender.yaml`."""

    model_config = ConfigDict(extra="forbid")

    bid_number: str = Field(min_length=1)
    gem_bid_id: str | int | None = None
    item_category: str | None = None
    total_quantity: int | None = None
    ministry: str | None = None
    source: str | None = None
    user_label: str | None = None
    files: list[ManifestFile] = Field(min_length=1)

    def stored_files(self) -> list[ManifestFile]:
        """Entries that have a file on disk."""
        return [f for f in self.files if f.file is not None]

    def known_urls(self) -> set[str]:
        """Every URL already recorded (stored, empty or duplicate)."""
        return {f.url for f in self.files if f.url}


class DiscoveredLink(BaseModel):
    """A link found inside a tender file, classified by the link rules."""

    model_config = ConfigDict(frozen=True)

    url: str
    found_in: str = Field(description="File name the link was found in.")
    depth: int = Field(ge=1, description="1 = linked from bid.pdf.")
    role: str | None = Field(description="Matched rule's role, or None if no rule matched.")
    action: LinkAction


class FetchedFile(BaseModel):
    """Result of downloading one link."""

    model_config = ConfigDict(frozen=True)

    url: str
    content: bytes
    content_type: str | None
    fetched_at: datetime
