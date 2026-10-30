"""Tag every chunk with the product type (and sub-type) it is about.

Retrieval filters on product type before anything else (a ball-valve requirement never looks
at gate-valve chunks; CLAUDE.md section 7, B1/B3). A wrong tag would hide real evidence, so
tagging is conservative:

1. A catalogue declares the product types it covers in `sources.yaml`; a chunk can only get one
   of those. (Measured on the real catalogues: tagging by keywords alone put 97 chunks of a
   triple-offset butterfly catalogue under "gate valve" after one sentence said "an
   alternative to gate valves".)
2. A document that declares a single type tags every chunk with it.
3. Otherwise a chunk is tagged by its section heading, else by its own text, when exactly one
   allowed type is named. Only a heading that appears on the chunk's own page counts: the
   chunker keeps a section open across page breaks (right for continuing tables), but on the
   real cryogenic brochure that put a page-1 heading "Triple-offset Butterfly Valves" over a
   page-2 company overview. Within the same section and page the last tag carries forward; it
   never crosses a heading or a page, and chunks without a heading never inherit.
4. A chunk still untagged takes its page's type when every tagged chunk on that page agrees and
   the page names that type in a section heading, or in at least `page_min_tagged` chunks.
   Catalogue pages show one product whose name is often only in the page title, above generic
   headings like "Dimensions - PN 16"; an overview page with one passing mention does not
   qualify, nor does a page showing two types.
5. Catalogues only: a spec-table page that names no product at all continues the product of
   the page before it, when that page is clearly one product (step 4's test, or itself a
   continuation). Catalogues introduce a product on one page and put its "Compliance
   Standards" / "Dimensions" tables on the next (measured: L&T Aquaseal pages 21, 23, 25, 27
   were "general", so a check-valve page was searched as butterfly-valve evidence). Prose
   pages never inherit: company overviews and feature lists stay general. Nor does a page
   whose own full text names another product (L&T API 602 p9 "Cryogenic Gate Valves" has
   its title outside the table and would otherwise inherit "check valve" from p8).
6. Anything unclear stays untagged ("general"): retrieval treats it as relevant to every type
   its document covers, so nothing is silently excluded.

Tender documents declare nothing, so every product type is allowed.

Sub-type: the one declared for that type, else the single sub-type named in the section or
chunk text, else none. Sub-types are never used to exclude evidence (B2).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.core.exceptions import SourceRegistryError
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import (
    SearchVocabulary,
    normalize_for_search,
    phrase_patterns,
)

Patterns = tuple[re.Pattern[str], ...]


def _list_patterns(
    ontologies: Sequence[Ontology], vocabulary: SearchVocabulary
) -> tuple[tuple[re.Pattern[str], dict[str, str]], ...]:
    """Patterns for lists sharing one noun: "gate, globe and check valves" names three types.

    Built from two-word synonyms "<head> <noun>" ("gate valve"): for each noun, a run of heads
    joined by spaces/"and"/"or" and ending in the noun.

    Returns:
        `(pattern, head word -> product type)` per noun.
    """
    heads_by_noun: dict[str, dict[str, str]] = {}
    for product in (p for o in ontologies for p in o.product_types):
        for synonym in product.synonyms:
            words = normalize_for_search(synonym, vocabulary).split()
            if len(words) == 2:
                heads_by_noun.setdefault(words[1], {}).setdefault(words[0], product.name)
    result = []
    for noun, heads in heads_by_noun.items():
        head = "|".join(re.escape(h) for h in sorted(heads))
        joiner = r"(?:\s+(?:and|or))?\s+"
        pattern = re.compile(rf"(?<!\S)(?:(?:{head}){joiner})+(?:{head})\s+{re.escape(noun)}(?!\S)")
        result.append((pattern, heads))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ProductMatcher:
    """Synonym patterns for every product type and sub-type of the loaded ontologies."""

    types: dict[str, Patterns]
    sub_types: dict[str, dict[str, Patterns]]
    lists: tuple[tuple[re.Pattern[str], dict[str, str]], ...] = ()

    @classmethod
    def from_ontologies(
        cls, ontologies: Sequence[Ontology], vocabulary: SearchVocabulary
    ) -> ProductMatcher:
        """Build the matcher; synonyms are normalized exactly like chunk `search_text`."""
        types: dict[str, Patterns] = {}
        subs: dict[str, dict[str, Patterns]] = {}
        for product in (p for o in ontologies for p in o.product_types):
            types[product.name] = phrase_patterns(product.synonyms, vocabulary)
            subs[product.name] = {
                s.name: phrase_patterns([s.name.replace("_", " "), *s.synonyms], vocabulary)
                for s in product.sub_types
            }
        return cls(types=types, sub_types=subs, lists=_list_patterns(ontologies, vocabulary))

    def types_in(self, search_text: str, allowed: frozenset[str]) -> set[str]:
        """Allowed product types named in normalized text, including in lists (A6)."""
        named = {t for t in allowed if any(p.search(search_text) for p in self.types[t])}
        for pattern, heads in self.lists:
            for match in pattern.finditer(search_text):
                named |= {heads[w] for w in match.group(0).split() if w in heads} & allowed
        return named

    def sub_types_in(self, product_type: str, search_text: str) -> set[str]:
        """Sub-types of `product_type` named in normalized text."""
        patterns = self.sub_types.get(product_type, {})
        return {s for s, pats in patterns.items() if any(p.search(search_text) for p in pats)}

    def validate(self, product_types: Sequence[str], sub_types: Sequence[str]) -> None:
        """Reject declared types or sub-types that the ontology does not define.

        Raises:
            SourceRegistryError: On an unknown name.
        """
        unknown = [t for t in product_types if t not in self.types]
        known_subs = {s for subs in self.sub_types.values() for s in subs}
        unknown += [s for s in sub_types if s not in known_subs]
        if unknown:
            raise SourceRegistryError(
                f"Unknown product types/sub-types in sources.yaml: {unknown}",
                details={"unknown": unknown},
            )


@dataclass(frozen=True, slots=True)
class TagInput:
    """What the tagger reads for one chunk (section and text already normalized)."""

    section: str
    search_text: str
    page: int = 0
    heading_on_page: bool = True
    is_table: bool = False


@dataclass(frozen=True, slots=True)
class ProductTag:
    """Product type and sub-type of one chunk (None = general / unknown)."""

    product_type: str | None
    sub_type: str | None


@dataclass(frozen=True, slots=True)
class Declared:
    """What a document declares it covers (empty for tender documents)."""

    product_types: tuple[str, ...] = ()
    sub_types: tuple[str, ...] = ()


def _only(found: set[str]) -> str | None:
    """The single element of `found`, or None if it has zero or several."""
    return next(iter(found)) if len(found) == 1 else None


def _sub_type(
    matcher: ProductMatcher, product_type: str, item: TagInput, declared: Declared
) -> str | None:
    """Declared sub-type for this type if exactly one, else the one named in the text."""
    of_type = set(matcher.sub_types.get(product_type, {}))
    declared_subs = {s for s in declared.sub_types if s in of_type}
    if declared_subs:
        return _only(declared_subs)
    return _only(matcher.sub_types_in(product_type, f"{item.section} {item.search_text}"))


@dataclass(frozen=True, slots=True)
class _Found:
    """First-pass type of one chunk and whether a heading (or the declaration) named it."""

    product_type: str | None
    from_heading: bool = False


def _first_pass(
    items: Sequence[TagInput], matcher: ProductMatcher, declared: Declared
) -> list[_Found]:
    """Product type per chunk from declaration, section heading, own text, or section carry."""
    allowed = frozenset(declared.product_types or matcher.types)
    single = next(iter(allowed)) if declared.product_types and len(allowed) == 1 else None
    found: list[_Found] = []
    carried: tuple[str, int, str | None] = ("", 0, None)  # section, page, type of last tag
    for item in items:
        local_section = item.section if item.heading_on_page else ""
        heading = single or _only(matcher.types_in(local_section, allowed))
        same_context = local_section and carried[:2] == (local_section, item.page)
        product_type = heading or (
            _only(matcher.types_in(item.search_text, allowed))
            or (carried[2] if same_context else None)
        )
        if product_type is not None:
            carried = (local_section, item.page, product_type)
        found.append(_Found(product_type, from_heading=heading is not None))
    return found


def _page_types(
    items: Sequence[TagInput], found: list[_Found], min_tagged: int
) -> dict[int, str | None]:
    """Each page's single product type, if its tagged chunks agree and the evidence is strong."""
    per_page: dict[int, list[_Found]] = {}
    for item, f in zip(items, found, strict=True):
        if f.product_type is not None:
            per_page.setdefault(item.page, []).append(f)
    page_type: dict[int, str | None] = {}
    for page, tagged in per_page.items():
        types = {f.product_type for f in tagged}
        strong = any(f.from_heading for f in tagged) or len(tagged) >= min_tagged
        page_type[page] = next(iter(types)) if len(types) == 1 and strong else None
    return page_type


def _continue_table_pages(
    items: Sequence[TagInput],
    types: list[str | None],
    page_type: dict[int, str | None],
    page_names: Mapping[int, set[str]],
) -> list[str | None]:
    """Step 5: an all-untagged table page takes the clear type of the page before it.

    `page_names` holds the allowed product types each page's full text names; a page naming
    any type other than the inherited one is left alone.
    """
    pages: dict[int, list[int]] = {}
    for i, item in enumerate(items):
        pages.setdefault(item.page, []).append(i)
    known = dict(page_type)
    result = list(types)
    for page in sorted(pages):
        indexes = pages[page]
        tables = sum(items[i].is_table for i in indexes)
        untagged = all(result[i] is None for i in indexes)
        inherited = known.get(page - 1)
        other = page_names.get(page, set()) - {inherited}
        if untagged and tables > len(indexes) - tables and inherited is not None and not other:
            for i in indexes:
                result[i] = inherited
            known[page] = inherited
    return result


def tag_chunks(
    items: Sequence[TagInput],
    matcher: ProductMatcher,
    declared: Declared,
    page_min_tagged: int,
    page_texts: Mapping[int, str] | None = None,
) -> list[ProductTag]:
    """Tag chunks of one document, in reading order (see module docstring for the rules).

    Args:
        items: Each chunk's normalized section heading, search text and page, in reading order.
        matcher: Product synonym patterns.
        declared: The document's declared product types and sub-types.
        page_min_tagged: Tagged chunks a page needs before its untagged chunks inherit.
        page_texts: Normalized full text per page number (step 5's guard); without it no
            page inherits from the previous one.

    Returns:
        One tag per chunk.
    """
    found = _first_pass(items, matcher, declared)
    page_type = _page_types(items, found, page_min_tagged)
    types = [
        f.product_type or page_type.get(item.page) for item, f in zip(items, found, strict=True)
    ]
    if declared.product_types and page_texts is not None:
        allowed = frozenset(declared.product_types)
        names = {page: matcher.types_in(text, allowed) for page, text in page_texts.items()}
        types = _continue_table_pages(items, types, page_type, names)
    return [
        ProductTag(
            product_type=t,
            sub_type=_sub_type(matcher, t, item, declared) if t is not None else None,
        )
        for item, t in zip(items, types, strict=True)
    ]
