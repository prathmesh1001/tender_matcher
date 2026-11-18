"""Read GeM Marketplace listings for one product type: search -> categories -> listings -> stored.

Stages, each its own small function: search GeM with the items' words and keep the categories
that fit the type (`find_category_pages`; the hand-listed categories of gem_marketplace.yaml
only when the search finds none), list those pages' product links ranked by the items' words,
skip the ones already stored, fetch each page (robots.txt and the pause are the fetcher's job),
freeze the HTML in the file store, parse it, check it, then hand it to `save` (ingestion). At
most `max_categories` categories and `max_listings` product pages are read per call. A listing
that fails any step is reported with the reason and never stored.
"""

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.core.exceptions import AppError, LinkFetchError
from app.discovery.gem_parser import GemListing, listing_urls, parse_listing, search_categories
from app.discovery.gem_search import fitting_categories, rank_listings
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
    page_urls: Sequence[str]  # category pages to read (the hand-listed ones before a search)
    max_listings: int
    snapshot_prefix: str
    empty_values: Sequence[str] = ()
    excludes: Sequence[str] = ()  # GeM look-alikes that are not the type
    search_urls: Sequence[str] = ()  # keyword search pages, narrowest first
    max_categories: int = 3
    rank_words: frozenset[str] = frozenset()  # item title words; products holding more go first
    base_url: str = ""


@dataclass(frozen=True, slots=True)
class CategoryPages:
    """Category pages chosen for a check, and where they came from."""

    page_urls: list[str]
    names: list[str]
    source: str  # "search", "configured" or "none"


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


def find_category_pages(request: DiscoveryRequest, deps: DiscoveryDeps) -> CategoryPages:
    """Category pages for the type: from GeM's keyword search, else the hand-listed ones.

    Every search page is read in turn until `max_categories` fitting categories are found; a
    search page that cannot be read is logged and skipped.

    Args:
        request: Search pages, hand-listed pages, limits.
        deps: Fetcher and the listing check.

    Returns:
        The pages and their category names (slugs for hand-listed ones).
    """
    found: dict[str, str] = {}
    for url in request.search_urls:
        if len(found) >= request.max_categories:
            break
        try:
            fetched = deps.fetcher.get(url)
        except LinkFetchError as exc:
            logger.warning("gem search not read", extra={"url": url, "error": exc.message})
            continue
        links = search_categories(fetched.content.decode("utf-8", errors="replace"))
        for link in fitting_categories(links, request.product_type, deps.check, request.excludes):
            found.setdefault(page_url(request.base_url, link.slug), link.name)
    if found:
        pages = list(found)[: request.max_categories]
        return CategoryPages(pages, [found[p] for p in pages], "search")
    if request.page_urls:
        pages = list(request.page_urls)
        return CategoryPages(pages, [_slug(p) for p in pages], "configured")
    return CategoryPages([], [], "none")


def _slug(page: str) -> str:
    """The category slug of a category page URL."""
    return page.rstrip("/").removesuffix("/search").rsplit("/", 1)[-1]


def candidate_urls(request: DiscoveryRequest, deps: DiscoveryDeps) -> list[ListingOutcome | str]:
    """Listing URLs to read, best-ranked first, at most `max_listings`; stored ones reported.

    Args:
        request: What to look for (`page_urls` are the category pages to read).
        deps: Fetcher, listing check and the stored-URL check.

    Returns:
        URLs to read, and outcomes for those skipped as already stored.
    """
    urls: list[str] = []
    for page in request.page_urls:
        fetched = deps.fetcher.get(page)
        html = fetched.content.decode("utf-8", errors="replace")
        urls += [u for u in listing_urls(html, page) if u not in urls]
    out: list[ListingOutcome | str] = []
    to_read = 0
    for url in rank_listings(urls, request.rank_words, deps.check):
        if to_read >= request.max_listings:
            break
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


def recheck_stored(
    html: str, url: str, request: DiscoveryRequest, check: ListingCheck
) -> str | None:
    """Why a listing stored by an earlier check fails today's check, or None if it still passes.

    Args:
        html: The listing's frozen HTML.
        url: Its URL.
        request: The product type, empty values and look-alikes of this check.
        check: Today's listing check.

    Returns:
        The rejection reason, or None.
    """
    listing = parse_listing(html, url, request.empty_values)
    return check.rejection(listing, request.product_type, request.excludes)


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
