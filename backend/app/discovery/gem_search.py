"""Search GeM by a tender's own items: search words, fitting categories, product ranking.

Pure functions; fetching is the pipeline's job.

- Search words come from the items: the product type plus the sub-types their titles name
  ("pneumatic control valve"), then the product type alone as a wider second search.
- A category from the search is kept only if its name names the product type and no other
  product instead (the listing check's rule): "Solenoid Valve" and "Directional Control Valve"
  are not control valves.
- `rank_words` gives the item title words that tell products apart; the cards of the kept
  categories are ranked and picked per item in `card_fit` (sub-type, size, title words).
"""

from collections.abc import Iterable, Sequence
from urllib.parse import quote

from app.discovery.gem_parser import CategoryLink
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
