"""Read GeM Marketplace listings for one product type: category pages -> listings -> stored.

Stages, each its own small function: list the category pages' product links, skip the ones
already stored, fetch each page (robots.txt and the pause are the fetcher's job), freeze the
HTML in the file store, parse it, check it, then hand it to `save` (ingestion). At most
`max_listings` product pages are read per call. A listing that fails any step is reported with
the reason and never stored.
"""

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.core.exceptions import AppError, LinkFetchError
from app.discovery.gem_parser import GemListing, listing_urls, parse_listing
from app.discovery.listing_check import ListingCheck
from app.discovery.polite_fetcher import PageFetcher
from app.ingestion.gem_listing import listing_name
from app.ingestion.pipeline import IngestOutcome, IngestStatus, ListingSource
from app.schemas.tender_files import FetchedFile
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

UNBRANDED = "Unbranded"


@dataclass(frozen=True, slots=True)
class ListingOutcome:
    """What happened to one listing."""

    url: str
    kept: bool
    reason: str
    title: str = ""
    brand: str | None = None
    model: str | None = None
    snapshot_ref: str | None = None

    def as_json(self) -> dict[str, Any]:
        """The outcome as stored on the discovery job."""
        return {
            "url": self.url,
            "kept": self.kept,
            "reason": self.reason,
            "title": self.title,
            "brand": self.brand,
            "model": self.model,
            "snapshot_ref": self.snapshot_ref,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    """One product type to look for, and where."""

    product_type: str
    category: str
    page_urls: Sequence[str]
    max_listings: int
    snapshot_prefix: str
    empty_values: Sequence[str] = ()
    excludes: Sequence[str] = ()  # GeM look-alikes that are not the type


@dataclass(frozen=True, slots=True)
class DiscoveryDeps:
    """What the pipeline reads and writes through."""

    fetcher: PageFetcher
    store: FileStore
    check: ListingCheck
    known: Callable[[str], bool]  # True if a listing URL is already stored
    save: Callable[[ListingSource, GemListing], IngestOutcome]


def page_url(base_url: str, slug: str) -> str:
    """The category page of a GeM category slug."""
    return f"{base_url.rstrip('/')}/{slug.strip('/')}/search"


def snapshot_ref(prefix: str, product_type: str, sha256: str) -> str:
    """File store reference of a listing's frozen HTML."""
    return f"{prefix}/{product_type}/{sha256}.html"


def candidate_urls(request: DiscoveryRequest, deps: DiscoveryDeps) -> list[ListingOutcome | str]:
    """Listing URLs to read, in category order, at most `max_listings`; stored ones reported.

    Args:
        request: What to look for.
        deps: Fetcher and the stored-URL check.

    Returns:
        URLs to read, and outcomes for those skipped as already stored.
    """
    out: list[ListingOutcome | str] = []
    to_read = 0
    for page in request.page_urls:
        fetched = deps.fetcher.get(page)
        for url in listing_urls(fetched.content.decode("utf-8", errors="replace"), page):
            if to_read >= request.max_listings:
                return out
            if deps.known(url):
                out.append(ListingOutcome(url, kept=False, reason="already stored (duplicate)"))
                continue
            out.append(url)
            to_read += 1
    return out


def read_listing(
    url: str, request: DiscoveryRequest, deps: DiscoveryDeps, seen: dict[str, str]
) -> ListingOutcome:
    """Fetch, freeze, parse, check and store one listing.

    Args:
        url: The product page.
        request: What is looked for.
        deps: Fetcher, store, check and save.
        seen: Brand/model name -> URL of listings kept in this call (duplicates are skipped).

    Returns:
        What happened.
    """
    try:
        fetched = deps.fetcher.get(url)
    except LinkFetchError as exc:
        return ListingOutcome(url, kept=False, reason=exc.message)
    listing = parse_listing(
        fetched.content.decode("utf-8", errors="replace"), url, request.empty_values
    )
    base = ListingOutcome(url, False, "", listing.title, listing.brand, listing.model)
    rejected = deps.check.rejection(listing, request.product_type, request.excludes)
    if rejected:
        return replace(base, reason=rejected)
    name = listing_name(listing).casefold()
    if name in seen:
        return replace(base, reason=f"duplicate of {seen[name]} (same brand and model)")
    return _store(listing, fetched, request, deps, seen, base)


def _store(
    listing: GemListing,
    fetched: FetchedFile,
    request: DiscoveryRequest,
    deps: DiscoveryDeps,
    seen: dict[str, str],
    base: ListingOutcome,
) -> ListingOutcome:
    """Freeze the HTML and store the listing as a document."""
    sha256 = hashlib.sha256(fetched.content).hexdigest()
    ref = snapshot_ref(request.snapshot_prefix, request.product_type, sha256)
    deps.store.write(ref, fetched.content)
    source = ListingSource(
        manufacturer=listing.brand or UNBRANDED,
        category=request.category,
        product_type=request.product_type,
        title=listing.title or listing_name(listing),
        url=listing.url,
        fetched_at=fetched.fetched_at,
        sha256=sha256,
        snapshot_ref=ref,
    )
    try:
        outcome = deps.save(source, listing)
    except AppError as exc:
        return replace(base, reason=f"not stored: {exc.message}", snapshot_ref=ref)
    if outcome.status is IngestStatus.SKIPPED_EXISTING:
        return replace(base, reason="already stored (duplicate)", snapshot_ref=ref)
    seen[listing_name(listing).casefold()] = listing.url
    return replace(base, kept=True, reason="kept", snapshot_ref=ref)


def discover(request: DiscoveryRequest, deps: DiscoveryDeps) -> list[ListingOutcome]:
    """Read and store GeM listings for one product type.

    Args:
        request: Product type, category pages and limits.
        deps: Fetcher, file store, listing check, stored-URL check and ingestion.

    Returns:
        One outcome per listing considered, in reading order.

    Raises:
        LinkFetchError: If a category page cannot be read (robots.txt or network).
    """
    seen: dict[str, str] = {}
    outcomes: list[ListingOutcome] = []
    for candidate in candidate_urls(request, deps):
        if isinstance(candidate, ListingOutcome):
            outcomes.append(candidate)
            continue
        outcome = read_listing(candidate, request, deps, seen)
        logger.info("gem listing", extra={"url": candidate, "kept": outcome.kept})
        outcomes.append(outcome)
    return outcomes
