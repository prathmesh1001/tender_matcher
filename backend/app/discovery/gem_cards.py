"""Product cards of a GeM Marketplace category page: what is known before a listing is opened.

Checked by hand on 2026-11-19 against real category pages (butterfly valves IS 13095, mineral
insulated thermocouples). Each product is a `<div class="variant-wrapper ...">` holding:

- `<span class="variant-title"><a href="...p-<id>-<variant>-cat.html" title="Full title">`;
- `<span class="sold_as oem">` or `<span class="sold_as resellers authorized">` (how it is sold);
- a seller rating band `<div class="overall-rating"> ... <span class="badge">4.0 - 4.49</span>`;
- `<a class="badge assesment assessed">` or `... not-assessed` (vendor assessment);
- `<div class="variant-brand"> Brand: ZOLOTO</div>`.

A page holds 12 cards; `?page=N` gives the next ones and `"total_results":N` says how many the
category has. Only the standard library's `html.parser` is used. Every function here is pure.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

_LISTING_LINK: Final = re.compile(r"/p-\d+-\d+-cat\.html$")
_SPACES: Final = re.compile(r"\s+")
_RATING: Final = re.compile(r"\d+(?:\.\d+)?")
_TOTAL: Final = re.compile(r'"total_results"\s*:\s*(\d+)')
_BRAND_LABEL: Final = re.compile(r"^\s*brand\s*:\s*", re.IGNORECASE)
# Registered / trade mark signs printed after a brand ("AAVAD®", "AAVAD (R)", "ZOLOTO™").
_MARKS: Final = re.compile(r"\s*(?:[®™©]|\((?:r|tm|c)\))\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ListingCard:
    """One product card of a category page."""

    url: str
    title: str = ""
    brand: str | None = None
    sold_as: tuple[str, ...] = ()  # "oem", or "resellers" + "authorized"
    rating: float | None = None  # lower end of the seller rating band
    assessed: bool | None = None  # vendor assessment badge


def clean_brand(text: str) -> str | None:
    """A brand without its "Brand:" label, trade mark sign or company name; None if empty.

    GeM sometimes prints "brand--company" ("ROCKWELL PUMPS--ROCKWELL PUMPS AND MOTORS PRIVATE
    LIMITED"); the brand is the part before "--".

    Args:
        text: The brand as printed ("Brand: AAVAD®").

    Returns:
        The brand ("AAVAD"), or None.
    """
    brand = _BRAND_LABEL.sub("", _SPACES.sub(" ", text)).split("--")[0]
    brand = _MARKS.sub("", brand).strip()
    return brand or None


def known_brand(brand: str | None, empty_values: Sequence[str]) -> str | None:
    """The brand, or None when the seller typed a placeholder ("NA", "N/A", "-").

    Args:
        brand: A cleaned brand (`clean_brand`).
        empty_values: Values that say nothing (config `empty_values`).

    Returns:
        The brand, or None.
    """
    if brand is None or brand.casefold() in {v.casefold() for v in empty_values}:
        return None
    return brand


def total_results(html: str) -> int | None:
    """How many products the category has, as the page states it; None if not stated."""
    match = _TOTAL.search(html)
    return int(match.group(1)) if match else None


@dataclass(slots=True)
class _Card:
    """A card being read."""

    url: str = ""
    title: str = ""
    brand: list[str] = field(default_factory=list)
    sold_as: tuple[str, ...] = ()
    rating: list[str] = field(default_factory=list)
    assessed: bool | None = None


class _CardParser(HTMLParser):
    """Every `div.variant-wrapper` card of a category page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[_Card] = []
        self._target: list[str] | None = None
        self._target_tag = ""
        self._in_rating = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "div" and "variant-wrapper" in classes:
            self.cards.append(_Card())
            return
        if not self.cards:
            return
        card = self.cards[-1]
        if tag == "a":
            self._link(card, a, classes)
        elif tag == "span" and "sold_as" in classes:
            card.sold_as = tuple(c.casefold() for c in classes if c != "sold_as")
        elif tag == "div" and "overall-rating" in classes:
            self._in_rating = True
        elif tag == "span" and "badge" in classes and self._in_rating:
            self._start(card.rating, tag)
        elif tag == "div" and "variant-brand" in classes:
            self._start(card.brand, tag)

    def _link(self, card: _Card, a: dict[str, str | None], classes: list[str]) -> None:
        href = a.get("href") or ""
        if not card.url and _LISTING_LINK.search(urlsplit(href).path):
            card.url = href
        if a.get("title") and not card.title and _LISTING_LINK.search(urlsplit(href).path):
            card.title = _SPACES.sub(" ", a.get("title") or "").strip()
        if "assesment" in classes:  # GeM's spelling
            card.assessed = "assessed" in classes

    def _start(self, target: list[str], tag: str) -> None:
        self._target, self._target_tag = target, tag

    def handle_endtag(self, tag: str) -> None:
        if self._target is not None and tag == self._target_tag:
            self._target = None
            self._in_rating = False

    def handle_data(self, data: str) -> None:
        if self._target is not None:
            self._target.append(data)


def _without_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _rating(parts: list[str]) -> float | None:
    """Lower end of a rating band ("4.0 - 4.49" -> 4.0)."""
    match = _RATING.search(" ".join(parts))
    return float(match.group(0)) if match else None


def listing_cards(html: str, page_url: str) -> list[ListingCard]:
    """Product cards of a category page, in page order, each listing once.

    Args:
        html: The category page.
        page_url: Where it was read from (relative links resolve against it).

    Returns:
        The cards; empty if the page has none (an older layout: use `listing_urls`).
    """
    parser = _CardParser()
    parser.feed(html)
    parser.close()
    out: dict[str, ListingCard] = {}
    for card in parser.cards:
        if not card.url:
            continue
        url = _without_fragment(urljoin(page_url, card.url))
        out.setdefault(
            url,
            ListingCard(
                url=url,
                title=card.title,
                brand=clean_brand(" ".join(card.brand)),
                sold_as=card.sold_as,
                rating=_rating(card.rating),
                assessed=card.assessed,
            ),
        )
    return list(out.values())
