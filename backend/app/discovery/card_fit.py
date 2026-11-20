"""Which GeM product cards fit which tender item: sub-type first, then size, then title words.

One GeM check covers every tender item of a product type, and those items differ: "GATE VALVE
80MM" and "KNIFE EDGE GATE VALVE 200MM" are both `gate_valve`. Ranking the cards once for all
items lets one item's look-alikes fill every slot (knife-edge listings for wedge gate items,
80 mm listings for a 200 mm item). So each distinct item gets its own wish (`ItemWant`), every
card is scored against each wish, and the listings to open are picked in turns, one per item
per round, so each item gets its best cards.

Card fit against one item, compared in this order (higher is better):

- sub-type: the card names one of the item's sub-types (1); names none (0); names another
  sub-type of the type (-1). An item naming no sub-type prefers cards naming none.
- size: the card names the item's size (1); names no size, or the item states none (0); names
  only other sizes (-1). Sizes are compared in millimetres: "200MM", "Size 200 millimeter",
  "DN 200", "NB 200" and "8 inch" (ontology `nps_to_dn`) are the same. A bare number with no
  unit ("Butterfly Valves 125") is never read as a size.
- title words: how many of the item's title words the card holds.

Ties go to how the product is sold, then seller rating, then vendor assessment, then page
order. Pure functions; fetching is the pipeline's job.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from app.discovery.gem_cards import ListingCard
from app.discovery.gem_parser import listing_slug_words
from app.discovery.listing_check import ListingCheck
from app.retrieval.text_normalizer import fold_text, normalize_for_search
from app.tender.normalizer import title_size

_MM: Final = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:mm|millimet(?:er|re)s?)\b")
_DN: Final = re.compile(r"\b(?:dn|nb)\s*-?\s*(\d+)\b")
_INCH: Final = re.compile(
    r"(?<![\w./-])(\d+[\s-]\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?:\"|''|inch(?:es)?\b|in\b)"
)

Fit = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class ItemWant:
    """What one tender item looks for in a card."""

    words: frozenset[str]  # normalized title words that tell products apart
    sub_types: frozenset[str] = frozenset()  # ontology sub-types the title names
    sizes_mm: frozenset[int] = frozenset()  # the size the title states, in mm
    # False when no item title is known: no sub-type is preferred or held against a card.
    judge_sub_type: bool = True


def _inches(text: str) -> Fraction | None:
    """Inches as written ("2", "1/2", "1-1/2", "1 1/2", "1.5") exactly; None if not a number."""
    whole, _, frac = text.strip().replace(" ", "-").partition("-")
    try:
        return Fraction(whole) + Fraction(frac) if frac else Fraction(whole)
    except ValueError, ZeroDivisionError:
        return None


def sizes_mm(text: str, nps_to_dn: Mapping[str, int]) -> frozenset[int]:
    """Every nominal size a text states, in millimetres (DN); numbers without a unit are not.

    Args:
        text: A card or item title.
        nps_to_dn: Inch size -> DN table (ontology `nps_to_dn`, keys like "1-1/2").

    Returns:
        The sizes in mm.
    """
    folded = fold_text(text)
    found = {round(float(m.group(1))) for m in _MM.finditer(folded)}
    found |= {int(m.group(1)) for m in _DN.finditer(folded)}
    table = {v: dn for k, dn in nps_to_dn.items() if (v := _inches(k)) is not None}
    for m in _INCH.finditer(folded):
        inches = _inches(m.group(1))
        if inches is not None and inches in table:
            found.add(table[inches])
    return frozenset(found)


def item_wants(
    product_type: str,
    titles: Iterable[str],
    check: ListingCheck,
    words_of: Callable[[str], frozenset[str]],
) -> list[ItemWant]:
    """One wish per distinct item title (titles asking the same thing give one wish).

    Args:
        product_type: The ontology product type, or a title key.
        titles: Titles of the tender items of that type.
        check: Gives the sub-type matcher, vocabulary and size table.
        words_of: Normalized title words that tell products apart (`gem_search.rank_words`).

    Returns:
        The wishes, in first-title order.
    """
    out: list[ItemWant] = []
    for title in titles:
        text = normalize_for_search(title, check.vocabulary)
        size = title_size(title)
        want = ItemWant(
            words=words_of(title),
            sub_types=frozenset(check.matcher.sub_types_in(product_type, text)),
            sizes_mm=sizes_mm(size, check.nps_to_dn) if size else frozenset(),
        )
        if want not in out:
            out.append(want)
    return out


def _signed(wanted: frozenset[object], named: Iterable[object]) -> int:
    """1 if the card names a wanted value, -1 if it names only others, else 0."""
    names = set(named)
    if names & wanted:
        return 1
    return -1 if names else 0


def card_fit(card: ListingCard, want: ItemWant, product_type: str, check: ListingCheck) -> Fit:
    """(sub-type, size, title words) fit of a card's product title (else its URL name)."""
    text = card.title or listing_slug_words(card.url)
    norm = normalize_for_search(text, check.vocabulary)
    subs = check.matcher.sub_types_in(product_type, norm)
    judged = want.judge_sub_type and (subs or want.sub_types)
    sub = _signed(want.sub_types, subs) if judged else 0
    size = _signed(want.sizes_mm, sizes_mm(text, check.nps_to_dn)) if want.sizes_mm else 0
    return sub, size, len(want.words.intersection(norm.split()))


def _seller_rank(card: ListingCard, seller_rank: Sequence[str]) -> int:
    """Position of the card's best "sold as" class in the configured order; unknown last."""
    order = [s.casefold() for s in seller_rank]
    return min((order.index(s) for s in card.sold_as if s in order), default=len(order))


def rank_for_item(
    cards: Sequence[ListingCard],
    want: ItemWant,
    product_type: str,
    check: ListingCheck,
    seller_rank: Sequence[str] = (),
) -> list[ListingCard]:
    """Cards best first for one item: fit, how it is sold, rating, assessment; ties keep order.

    Args:
        cards: Category-page cards in page order.
        want: The item's wish.
        product_type: The ontology product type, or a title key.
        check: Sub-type matcher, vocabulary and size table.
        seller_rank: "Sold as" classes, best first (config `seller_rank`).

    Returns:
        The cards, re-ordered.
    """

    def key(card: ListingCard) -> tuple[int, int, int, int, float, bool]:
        sub, size, words = card_fit(card, want, product_type, check)
        rank = _seller_rank(card, seller_rank)
        return -sub, -size, -words, rank, -(card.rating or 0.0), card.assessed is not True

    return sorted(cards, key=key)


@dataclass(slots=True)
class _Picker:
    """Running state of `pick_for_items`."""

    max_new: int
    max_per_brand: int
    known: Callable[[str], bool]
    picked: list[ListingCard]
    per_brand: dict[str, int]
    new: int = 0

    def take(self, card: ListingCard) -> bool:
        """Pick the card if its brand has room; a stored one costs no read."""
        brand = (card.brand or "").casefold()
        if brand and self.per_brand.get(brand, 0) >= self.max_per_brand:
            return False
        stored = self.known(card.url)
        if not stored and self.new >= self.max_new:
            return False
        self.per_brand[brand] = self.per_brand.get(brand, 0) + 1
        self.new += not stored
        self.picked.append(card)
        return True


def pick_for_items(
    ranked: Sequence[Sequence[ListingCard]],
    max_new: int,
    max_per_brand: int,
    known: Callable[[str], bool],
) -> list[ListingCard]:
    """Cards to open, taken in turns: each item's next best card, item after item.

    A card already stored counts for its item without a read; at most `max_new` cards are new.

    Args:
        ranked: Each item's cards, best first (`rank_for_item`).
        max_new: Most cards not yet stored to pick.
        max_per_brand: Most cards per brand (compared case-insensitively; no brand is free).
        known: True if a listing URL is already stored.

    Returns:
        The picked cards, in picking order, each once.
    """
    picker = _Picker(max_new, max_per_brand, known, [], {})
    queues = [list(r) for r in ranked]
    seen: set[str] = set()
    while picker.new < max_new and any(queues):
        for queue in queues:
            while queue:
                card = queue.pop(0)
                if card.url not in seen and picker.take(card):
                    seen.add(card.url)
                    break
    return picker.picked
