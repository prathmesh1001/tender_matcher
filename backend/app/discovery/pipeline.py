"""Read GeM Marketplace listings for one product type: search -> categories -> listings -> stored.

Stages, each its own small function: search GeM with the items' words and keep the categories
that fit the type (`find_category_pages`; the hand-listed categories of gem_marketplace.yaml
only when the search finds none), read up to `max_pages` result pages of each and rank their
product cards (product fit, how it is sold, seller rating; at most `max_per_brand` per brand),
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
from app.discovery.gem_cards import ListingCard, listing_cards, total_results
from app.discovery.gem_parser import GemListing, listing_urls, parse_listing, search_categories
from app.discovery.gem_search import fitting_categories, pick_cards, rank_cards
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
    sold_as: str | None = None  # from the category card: "oem", "resellers authorized"
    seller_rating: float | None = None
    assessed: bool | None = None

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
            "sold_as": self.sold_as,
            "seller_rating": self.seller_rating,
            "assessed": self.assessed,
        }

    @classmethod
    def for_card(cls, card: ListingCard, reason: str) -> ListingOutcome:
        """A listing not read, with what its category card said."""
        return cls(
            card.url,
            kept=False,
            reason=reason,
            title=card.title,
            brand=card.brand,
            sold_as=" ".join(card.sold_as) or None,
            seller_rating=card.rating,
            assessed=card.assessed,
        )


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
    max_pages: int = 1  # result pages read per category (12 products each)
    max_per_brand: int = 1_000  # most listings opened per brand
    seller_rank: Sequence[str] = ()  # "sold as" classes, best first


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


def category_cards(page: str, max_pages: int, deps: DiscoveryDeps) -> list[ListingCard]:
    """Product cards of up to `max_pages` result pages of one category.

    Page 1 must be readable (else the check fails); a later page that cannot be read ends the
    reading. A page without cards (older layout) gives its product links as bare cards and no
    further page is read.

    Args:
        page: The category page (page 1).
        max_pages: Most result pages read.
        deps: The fetcher.

    Returns:
        The cards in page order.

    Raises:
        LinkFetchError: If page 1 cannot be read.
    """
    html = deps.fetcher.get(page).content.decode("utf-8", errors="replace")
    cards = listing_cards(html, page)
    if not cards:
        return [ListingCard(url) for url in listing_urls(html, page)]
    total = total_results(html)
    for number in range(2, max_pages + 1):
        if total is not None and len(cards) >= total:
            break
        url = f"{page}?page={number}"
        try:
            more = deps.fetcher.get(url).content.decode("utf-8", errors="replace")
        except LinkFetchError as exc:
            logger.warning("gem page not read", extra={"url": url, "error": exc.message})
            break
        known = {c.url for c in cards}
        new = [c for c in listing_cards(more, page) if c.url not in known]
        if not new:
            break
        cards += new
    return cards


def candidate_cards(
    request: DiscoveryRequest, deps: DiscoveryDeps
) -> list[ListingOutcome | ListingCard]:
    """Listings to read, best first, at most `max_listings`; stored ones ranked above reported.

    Args:
        request: What to look for (`page_urls` are the category pages to read).
        deps: Fetcher, listing check and the stored-URL check.

    Returns:
        Cards to read, and outcomes for better-ranked ones skipped as already stored.
    """
    cards: list[ListingCard] = []
    for page in request.page_urls:
        seen = {c.url for c in cards}
        cards += [c for c in category_cards(page, request.max_pages, deps) if c.url not in seen]
    ranked = rank_cards(cards, request.rank_words, deps.check, request.seller_rank)
    fresh = [c for c in ranked if not deps.known(c.url)]
    picked = pick_cards(fresh, request.max_listings, request.max_per_brand)
    last = ranked.index(picked[-1]) if picked else len(ranked) - 1
    chosen = {c.url for c in picked}
    return [
        c if c.url in chosen else ListingOutcome.for_card(c, "already stored (duplicate)")
        for c in ranked[: last + 1]
        if c.url in chosen or deps.known(c.url)
    ]


def read_listing(
    card: ListingCard, request: DiscoveryRequest, deps: DiscoveryDeps, seen: dict[str, str]
) -> ListingOutcome:
    """Fetch, freeze, parse, check and store one listing.

    Args:
        card: The product page's category card.
        request: What is looked for.
        deps: Fetcher, store, check and save.
        seen: Brand/model name -> URL of listings kept in this call (duplicates are skipped).

    Returns:
        What happened.
    """
    try:
        fetched = deps.fetcher.get(card.url)
    except LinkFetchError as exc:
        return ListingOutcome.for_card(card, exc.message)
    listing = parse_listing(
        fetched.content.decode("utf-8", errors="replace"), card.url, request.empty_values
    )
    base = replace(
        ListingOutcome.for_card(card, ""),
        title=listing.title,
        brand=listing.brand or card.brand,
        model=listing.model,
    )
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
    for candidate in candidate_cards(request, deps):
        if isinstance(candidate, ListingOutcome):
            outcomes.append(candidate)
            continue
        outcome = read_listing(candidate, request, deps, seen)
        logger.info("gem listing", extra={"url": candidate.url, "kept": outcome.kept})
        outcomes.append(outcome)
    return outcomes
