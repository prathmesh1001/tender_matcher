"""Search GeM by a tender's own items: search words, fitting categories, product ranking.

Pure functions; fetching is the pipeline's job.

- Search words come from the items: the product type plus the sub-types their titles name
  ("pneumatic control valve"), then the product type alone as a wider second search.
- A category from the search is kept only if its name names the product type and no other
  product instead (the listing check's rule): "Solenoid Valve" and "Directional Control Valve"
  are not control valves.
- Products of the kept categories are ranked from their category-page cards: item title words
  in the product title first (product fit), then how it is sold (config `seller_rank`: OEM
  before authorised reseller), the seller rating and the vendor assessment. Listings are then
  picked best first with at most a few per brand, so one brand cannot fill every slot. Pages
  without cards fall back to ranking by the URL name ("k-type-thermocouple").
"""

from collections.abc import Iterable, Sequence
from urllib.parse import quote

from app.discovery.gem_cards import ListingCard
from app.discovery.gem_parser import CategoryLink, listing_slug_words
from app.discovery.listing_check import ListingCheck
from app.retrieval.text_normalizer import normalize_for_search


def search_url(base_url: str, query: str) -> str:
    """GeM's keyword search page for a query."""
    return f"{base_url.rstrip('/')}/search?q={quote(query)}"


def type_phrase(product_type: str) -> str:
    """The product type as search words ("control_valve" -> "control valve")."""
    return product_type.replace("_", " ")


def search_queries(product_type: str, titles: Iterable[str], check: ListingCheck) -> list[str]:
    """Search words for the items of one product type, narrowest first, each once.

    Args:
        product_type: The ontology product type.
        titles: Titles of the tender items of that type.
        check: Gives the ontology matcher and vocabulary.

    Returns:
        "<sub-types> <type>" when the titles name sub-types, then "<type>".
    """
    subs: list[str] = []
    for title in titles:
        text = normalize_for_search(title, check.vocabulary)
        subs += sorted(check.matcher.sub_types_in(product_type, text))
    words = " ".join(type_phrase(s) for s in dict.fromkeys(subs))
    phrase = type_phrase(product_type)
    return list(dict.fromkeys([f"{words} {phrase}".strip(), phrase]))


def rank_words(
    product_type: str, titles: Iterable[str], check: ListingCheck, ignore: Iterable[str] = ()
) -> frozenset[str]:
    """Normalized title words that tell products of the type apart (not the type's own words).

    Args:
        product_type: The ontology product type.
        titles: Titles of the tender items of that type.
        check: Gives the vocabulary.
        ignore: Words that say nothing (config `rank_ignore_words`).

    Returns:
        The words.
    """
    vocabulary = check.vocabulary
    skip = set(normalize_for_search(type_phrase(product_type), vocabulary).split())
    skip |= {w for word in ignore for w in normalize_for_search(word, vocabulary).split()}
    words = {w for t in titles for w in normalize_for_search(t, vocabulary).split()}
    return frozenset(w for w in words if len(w) > 1 and w not in skip)


def fitting_categories(
    links: Iterable[CategoryLink],
    product_type: str,
    check: ListingCheck,
    excludes: Sequence[str] = (),
    title_words: frozenset[str] | None = None,
    tail_words: frozenset[str] = frozenset(),
) -> list[CategoryLink]:
    """Search categories whose name names the product type and no other product instead.

    For a title key, a qualifier before the key must also be a tender title word
    (`ListingCheck.qualifiers_named`): "Cadaverous Injector" is not the item "INJECTOR 74 HP".

    Args:
        links: Categories offered by a search page.
        product_type: The ontology product type, or a title key.
        check: The listing check.
        excludes: GeM look-alike phrases configured for the type.
        title_words: Normalized tender item title words; None skips the qualifier rule.
        tail_words: Normalized words that may follow a title key (filler, standards).

    Returns:
        The fitting categories, in page order.
    """
    keyed = title_words is not None and check.is_title_key(product_type)
    return [
        c
        for c in links
        if check.names_type_in(c.name, product_type)
        and check.foreign_in(c.name, product_type, excludes) is None
        and (
            not keyed
            or check.qualifiers_named(c.name, product_type, title_words or frozenset(), tail_words)
        )
    ]


def _fit(card: ListingCard, words: frozenset[str], check: ListingCheck) -> int:
    """How many item title words the card's product title (else its URL name) holds."""
    text = card.title or listing_slug_words(card.url)
    return len(words.intersection(normalize_for_search(text, check.vocabulary).split()))


def _seller_rank(card: ListingCard, seller_rank: Sequence[str]) -> int:
    """Position of the card's best "sold as" class in the configured order; unknown last."""
    order = [s.casefold() for s in seller_rank]
    return min((order.index(s) for s in card.sold_as if s in order), default=len(order))


def rank_cards(
    cards: Sequence[ListingCard],
    words: frozenset[str],
    check: ListingCheck,
    seller_rank: Sequence[str] = (),
) -> list[ListingCard]:
    """Cards best first: product fit, then how it is sold, seller rating, vendor assessment.

    Ties keep page order.

    Args:
        cards: Category-page cards in page order.
        words: Normalized item title words (`rank_words`).
        check: Gives the vocabulary.
        seller_rank: "Sold as" classes, best first (config `seller_rank`).

    Returns:
        The cards, re-ordered.
    """
    return sorted(
        cards,
        key=lambda c: (
            -_fit(c, words, check),
            _seller_rank(c, seller_rank),
            -(c.rating or 0.0),
            c.assessed is not True,
        ),
    )


def pick_cards(
    ranked: Sequence[ListingCard], max_listings: int, max_per_brand: int
) -> list[ListingCard]:
    """The best cards to open, at most `max_per_brand` per brand (a card without one is free).

    Args:
        ranked: Cards best first (`rank_cards`).
        max_listings: How many to pick.
        max_per_brand: Most picked per brand (brand compared case-insensitively).

    Returns:
        The picked cards, best first.
    """
    picked: list[ListingCard] = []
    per_brand: dict[str, int] = {}
    for card in ranked:
        if len(picked) >= max_listings:
            break
        brand = (card.brand or "").casefold()
        if brand and per_brand.get(brand, 0) >= max_per_brand:
            continue
        per_brand[brand] = per_brand.get(brand, 0) + 1
        picked.append(card)
    return picked
