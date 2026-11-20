"""Read GeM Marketplace pages (plain HTML, no JavaScript) into typed values.

Three page kinds are read, checked by hand on 2026-11-18 and 2026-11-19:

- a keyword search page (`/search?q=<words>`) lists the categories holding those words as
  `<li class="bn-link"><a href="/<category-slug>/search#/?q=...">Name<i>(Q3)</i></a></li>`
  (its product list needs JavaScript; the category list does not);
- a category page (`/<category-slug>/search`) lists products as links ending in
  `p-<id>-<variant>-cat.html` (their cards are read by `gem_cards`);
- a product page has the title in `<h1 itemprop="name">` with `<div class="brand">` and
  `<span class="model">` inside it (the brand's `<sup>R</sup>` mark is left out), and the
  seller's specification in `<div id="feature_groups">`: an `<h4>` per group, then rows
  `<td class="feature_name">Label</td><td>Value</td>`.

Only the standard library's `html.parser` is used. Every function here is pure.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.discovery.gem_cards import clean_brand

_LISTING_LINK: Final = re.compile(r"/p-\d+-\d+-cat\.html$")
_CATEGORY_LINK: Final = re.compile(r"^/([^/]+)/search$")
_SPACES: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SpecRow:
    """One specification row as the seller typed it."""

    group: str | None
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class GemListing:
    """One product page: what it says and where it came from."""

    url: str
    title: str
    brand: str | None
    model: str | None
    specs: tuple[SpecRow, ...]


def _clean(text: str) -> str:
    """Whitespace collapsed and trimmed."""
    return _SPACES.sub(" ", text).strip()


def _without_fragment(url: str) -> str:
    """The URL without `#...` (GeM puts the variant id there; the path already holds it)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


class _LinkCollector(HTMLParser):
    """Every `href` on a page, in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


@dataclass(frozen=True, slots=True)
class CategoryLink:
    """One category offered by a keyword search."""

    name: str
    slug: str


class _CategoryCollector(HTMLParser):
    """Name and href of every `li.bn-link > a` (the `<i>` quarter note left out)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, list[str]]] = []
        self._in_item = False
        self._in_link = False
        self._in_note = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "li":
            self._in_item = "bn-link" in (a.get("class") or "").split()
        elif tag == "a" and self._in_item and a.get("href"):
            self._in_link = True
            self.links.append((a.get("href") or "", []))
        elif tag == "i" and self._in_link:
            self._in_note = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "i":
            self._in_note = False
        elif tag == "a":
            self._in_link = False
        elif tag == "li":
            self._in_item = False

    def handle_data(self, data: str) -> None:
        if self._in_link and not self._in_note and self.links:
            self.links[-1][1].append(data)


def search_categories(html: str) -> list[CategoryLink]:
    """Categories a keyword search page offers, in page order, each slug once.

    Args:
        html: The search page (`/search?q=...`).

    Returns:
        The categories.
    """
    collector = _CategoryCollector()
    collector.feed(html)
    out: dict[str, CategoryLink] = {}
    for href, parts in collector.links:
        match = _CATEGORY_LINK.match(urlsplit(href).path)
        name = _clean(" ".join(parts))
        if match and name and match.group(1) not in out:
            out[match.group(1)] = CategoryLink(name=name, slug=match.group(1))
    return list(out.values())


def listing_category_words(url: str) -> str:
    """The listing's own GeM category from its URL as words.

    "/aluminium-phosphide-formulation-v3-conforming-6438/quickphos-.../p-1-2-cat.html" ->
    "aluminium phosphide formulation v3 conforming 6438".

    Args:
        url: A listing URL (`/<category>/<product-name>/p-<id>-<variant>-cat.html`).

    Returns:
        The words, or "" if the URL has no category part.
    """
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return parts[0].replace("-", " ") if len(parts) >= 3 else ""


def listing_slug_words(url: str) -> str:
    """The product-name part of a listing URL as words ("k-type-thermocouple" -> "k type ...").

    Args:
        url: A listing URL (`/<category>/<product-name>/p-<id>-<variant>-cat.html`).

    Returns:
        The words, or "" if the URL has no name part.
    """
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return parts[-2].replace("-", " ") if len(parts) >= 2 else ""


def listing_urls(html: str, page_url: str) -> list[str]:
    """Product page links of a category page, absolute, without fragment, first seen first.

    Args:
        html: The category page.
        page_url: Where it was read from (relative links resolve against it).

    Returns:
        The listing URLs, each once.
    """
    collector = _LinkCollector()
    collector.feed(html)
    out: list[str] = []
    for href in collector.hrefs:
        url = _without_fragment(urljoin(page_url, href))
        if _LISTING_LINK.search(urlsplit(url).path) and url not in out:
            out.append(url)
    return out


@dataclass(slots=True)
class _ProductState:
    """What the product-page parser has read so far."""

    title_parts: list[str] = field(default_factory=list)
    brand_parts: list[str] = field(default_factory=list)
    model_parts: list[str] = field(default_factory=list)
    item_model: str | None = None
    group: str | None = None
    group_parts: list[str] = field(default_factory=list)
    cells: list[list[str]] = field(default_factory=list)
    rows: list[SpecRow] = field(default_factory=list)


class _ProductParser(HTMLParser):
    """Title, brand, model and specification rows of one product page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.state = _ProductState()
        self._in_title = 0  # depth inside <h1 itemprop="name">, 0 = outside
        self._target: list[str] | None = None  # where text goes now
        self._in_features = 0  # depth inside <div id="feature_groups">
        self._in_cell = False
        self._in_h4 = False
        self._in_mark = False  # inside the brand's <sup> trade mark ("AAVAD<sup>R</sup>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "sup" and self._target is self.state.brand_parts:
            self._in_mark = True
        if tag == "h1" and a.get("itemprop") == "name":
            self._in_title = 1
            self._target = self.state.title_parts
        elif self._in_title:
            self._in_title += tag not in _VOID
            if "brand" in classes:
                self._target = self.state.brand_parts
            elif "model" in classes:
                self._target = self.state.model_parts
        if tag == "span" and a.get("itemprop") == "model":
            self._target = []
            self.state.item_model = ""
        if tag == "div" and a.get("id") == "feature_groups":
            self._in_features = 1
        elif self._in_features and tag not in _VOID:
            self._in_features += 1
        if self._in_features:
            self._feature_start(tag)

    def _feature_start(self, tag: str) -> None:
        if tag == "h4":
            self._in_h4 = True
            self.state.group_parts = []
        elif tag == "tr":
            self.state.cells = []
        elif tag == "td":
            self._in_cell = True
            self.state.cells.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag == "sup":
            self._in_mark = False
        if self.state.item_model == "" and tag == "span" and self._target is not None:
            self.state.item_model = _clean(" ".join(self._target)) or None
            self._target = None
        if self._in_title:
            self._in_title -= 1
            self._target = self.state.title_parts if self._in_title else None
        if self._in_features:
            self._feature_end(tag)
            self._in_features -= 1

    def _feature_end(self, tag: str) -> None:
        if tag == "h4":
            self._in_h4 = False
            self.state.group = _clean(" ".join(self.state.group_parts)) or None
        elif tag == "td":
            self._in_cell = False
        elif tag == "tr":
            cells = [_clean(" ".join(c)) for c in self.state.cells]
            if len(cells) >= 2 and cells[0]:
                self.state.rows.append(SpecRow(self.state.group, cells[0], cells[1]))

    def handle_data(self, data: str) -> None:
        if self._target is not None and not self._in_mark:
            self._target.append(data)
        if self._in_h4:
            self.state.group_parts.append(data)
        elif self._in_cell and self.state.cells:
            self.state.cells[-1].append(data)


_VOID: Final = frozenset({"br", "img", "input", "meta", "link", "hr", "wbr", "source"})


def parse_listing(html: str, url: str, empty_values: Sequence[str] = ()) -> GemListing:
    """Read one product page.

    Args:
        html: The product page.
        url: Where it was read from.
        empty_values: Values that say nothing ("NA"); rows holding only one are dropped.

    Returns:
        The listing; `specs` is empty if the page has no specification table.
    """
    parser = _ProductParser()
    parser.feed(html)
    parser.close()
    state = parser.state
    empty = {v.casefold() for v in empty_values}
    specs = tuple(r for r in state.rows if r.value and r.value.casefold() not in empty)
    model = state.item_model or _clean(" ".join(state.model_parts)).strip("()") or None
    return GemListing(
        url=_without_fragment(url),
        title=_clean(" ".join(state.title_parts)),
        brand=clean_brand(" ".join(state.brand_parts)),
        model=_clean(model) if model else None,
        specs=specs,
    )
