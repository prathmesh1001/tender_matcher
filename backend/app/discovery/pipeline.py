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
from urllib.parse import urlsplit

from app.core.exceptions import AppError, LinkFetchError
from app.discovery.card_fit import ItemWant, pick_for_items, rank_for_item
from app.discovery.gem_cards import ListingCard, known_brand, listing_cards, total_results
from app.discovery.gem_parser import GemListing, listing_urls, parse_listing, search_categories
from app.discovery.gem_search import fitting_categories, search_url
from app.discovery.listing_check import ListingCheck
from app.discovery.polite_fetcher import PageFetcher
from app.ingestion.gem_listing import listing_name
from app.ingestion.pipeline import IngestOutcome, IngestStatus, ListingSource
from app.retrieval.text_normalizer import normalize_for_search
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
    title_words: frozenset[str] | None = None  # every normalized item title word (title keys)
    buyer_categories: Sequence[str] = ()  # GeM categories the buyer selected (searched first)
    # A buyer category checked for the whole tender: its own listings are kept as they are
    # (`listing_rejection`); no keyword search, no product-name rules.
    trusted: bool = False
    tail_words: frozenset[str] = frozenset()  # words that may follow a title key in a category
    max_per_brand: int = 1_000  # most listings opened per brand
    seller_rank: Sequence[str] = ()  # "sold as" classes, best first
    # One wish per distinct item (sub-type, size, title words); cards are picked per item.
    item_wants: Sequence[ItemWant] = ()


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


def _buyer_pages(request: DiscoveryRequest, deps: DiscoveryDeps) -> dict[str, str]:
    """Pages of the buyer's own GeM categories that name the type, found by their exact name.

    The buyer picked them on GeM ("Stainless Steel Pump Impeller"), so a category naming the
    item's product and no other product needs no further fit rule; GeM is searched by the
    category's name and the category of that same name is taken.
    """
    found: dict[str, str] = {}
    check, product_type = deps.check, request.product_type
    for name in request.buyer_categories:
        # A trusted request is the buyer category itself: no product-name rules.
        if not request.trusted and (
            not check.names_type_in(name, product_type)
            or check.foreign_in(name, product_type, request.excludes)
        ):
            continue
        try:
            fetched = deps.fetcher.get(search_url(request.base_url, name))
        except LinkFetchError as exc:
            logger.warning(
                "gem buyer category not read", extra={"name": name, "error": exc.message}
            )
            continue
        wanted = " ".join(name.casefold().split())
        for link in search_categories(fetched.content.decode("utf-8", errors="replace")):
            if " ".join(link.name.casefold().split()) == wanted:
                found.setdefault(page_url(request.base_url, link.slug), link.name)
    return found


def find_category_pages(request: DiscoveryRequest, deps: DiscoveryDeps) -> CategoryPages:
    """Category pages for the type: buyer's categories, keyword search, else hand-listed ones.

    The buyer's categories that name the type come first (`_buyer_pages`). Then every search
    page is read in turn until `max_categories` fitting categories are found; a search page
    that cannot be read is logged and skipped.

    Args:
        request: Buyer categories, search pages, hand-listed pages, limits.
        deps: Fetcher and the listing check.

    Returns:
        The pages and their category names (slugs for hand-listed ones).
    """
    found = _buyer_pages(request, deps)
    source = "buyer" if found else "search"
    for url in request.search_urls:
        if len(found) >= request.max_categories:
            break
        try:
            fetched = deps.fetcher.get(url)
        except LinkFetchError as exc:
            logger.warning("gem search not read", extra={"url": url, "error": exc.message})
            continue
        links = search_categories(fetched.content.decode("utf-8", errors="replace"))
        fitting = fitting_categories(
            links,
            request.product_type,
            deps.check,
            request.excludes,
            request.title_words,
            request.tail_words,
        )
        for link in fitting:
            found.setdefault(page_url(request.base_url, link.slug), link.name)
    if found:
        pages = list(found)[: request.max_categories]
        return CategoryPages(pages, [found[p] for p in pages], source)
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
    """Listings to read, picked per item in turns (`card_fit`); stored ones are reported.

    Each item's cards are ranked by sub-type, size and title words; the items then take turns
    picking their next best card. A stored card counts for its item without a read; at most
    `max_listings` cards are read.

    Args:
        request: What to look for (`page_urls` are the category pages to read).
        deps: Fetcher, listing check and the stored-URL check.

    Returns:
        Cards to read, and outcomes for picked cards that are already stored, in picking order.
    """
    cards: list[ListingCard] = []
    for page in request.page_urls:
        seen = {c.url for c in cards}
        cards += [
            replace(c, brand=known_brand(c.brand, request.empty_values))
            for c in category_cards(page, request.max_pages, deps)
            if c.url not in seen
        ]
    wants = list(request.item_wants) or [ItemWant(request.rank_words, judge_sub_type=False)]
    ranked = [
        rank_for_item(cards, w, request.product_type, deps.check, request.seller_rank)
        for w in wants
    ]
    picked = pick_for_items(ranked, request.max_listings, request.max_per_brand, deps.known)
    return [
        ListingOutcome.for_card(c, "already stored (duplicate)") if deps.known(c.url) else c
        for c in picked
    ]


def listing_rejection(
    listing: GemListing, request: DiscoveryRequest, check: ListingCheck
) -> str | None:
    """Why a listing is not kept for this request, or None to keep it.

    A trusted request (a buyer category) keeps a listing that has enough specification rows
    and sits in one of the request's own category pages (its URL's category); category pages
    may show neighbouring products, which fail. Anything else is the listing check's call.

    Args:
        listing: A parsed listing.
        request: What is looked for.
        check: The listing check.

    Returns:
        A short reason, or None.
    """
    if not request.trusted:
        return check.rejection(listing, request.product_type, request.excludes)
    if len(listing.specs) < check.min_spec_rows:
        return f"only {len(listing.specs)} specification rows (need {check.min_spec_rows})"
    own = _listing_category(listing.url)
    if not own or not any(_same_category(_slug(p), own) for p in request.page_urls):
        return f"in GeM category {own or 'unknown'}, not a buyer category"
    return None


def _same_category(page_slug: str, own: str) -> bool:
    """True if a category page's slug is the listing's category.

    Search links carry the parent path ("industrial-pumps-...-stainless-steel-pump-impeller");
    listing URLs only the category's own slug ("stainless-steel-pump-impeller").
    """
    page, own = page_slug.strip("-"), own.strip("-")
    return page == own or page.endswith(f"-{own}")


def _listing_category(url: str) -> str:
    """The category slug a listing URL starts with ("/<category>/<name>/p-...")."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return parts[0] if len(parts) >= 3 else ""


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
    rejected = listing_rejection(listing, request, deps.check)
    if rejected:
        return replace(base, reason=rejected)
    name = listing_name(listing).casefold()
    if name in seen:
        return replace(base, reason=f"duplicate of {seen[name]} (same brand and model)")
    return _store(listing, fetched, request, deps, seen, base)


def listing_sub_types(
    listing: GemListing, product_type: str, check: ListingCheck
) -> tuple[str, ...]:
    """Ontology sub-types of the type that the listing's title or model names, sorted.

    Stored on the document, so a knife-edge gate valve listing is a sub-type match for a knife
    gate item and a wedge gate listing is not (CLAUDE.md B2); a listing naming none is general.

    Args:
        listing: A parsed listing.
        product_type: The type it is stored for.
        check: Gives the sub-type matcher and vocabulary.

    Returns:
        The sub-types.
    """
    text = " ".join(p for p in (listing.title, listing.model) if p)
    norm = normalize_for_search(text, check.vocabulary)
    return tuple(sorted(check.matcher.sub_types_in(product_type, norm)))


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
        sub_types=listing_sub_types(listing, request.product_type, deps.check),
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
    return listing_rejection(listing, request, check)


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
