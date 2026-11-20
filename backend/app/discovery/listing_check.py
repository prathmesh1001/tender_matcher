"""Whether a GeM listing may be kept as evidence for a product type.

A listing is kept only if it is about that product and says enough: its title or one of its
specification rows names the product type (ontology synonyms, matched on normalized text like
every other product check), it has at least the configured number of rows, and neither its title
nor its model names a different product. GeM category pages also list neighbouring products, so
the category alone proves nothing.

GeM builds a listing's title from its category ("Unbranded Control Valve Pneumatic Size 15"),
so the seller's model ("Festo Flow Control Valve GRLA-3") is often the only real product name.
Title and model are each read for foreign product names: another ontology product type, a
product of the category with no type (`other_products`), or a GeM look-alike configured for the
type (`exclude` in config/gem_marketplace.yaml). A part naming one is rejected unless it still
names the wanted type once the foreign names are taken out: "Thermocouple with Thermowell" is a
thermocouple, "Flow Control Valve" is not a control valve.

A name counts for the wanted type only where it names the product itself (ontology
`accessory_words`, `part_words`): before "with" ("Temperature Gauge with Thermowell" is no
thermowell) and not followed by a part, tool or service word ("Thermocouple Calibration
Furnace", "Valve Repair Kit"). A name may also hold a synonym's words apart or in another
order ("Submersible Sewerage Pump", "Centrifugal Cooling Water Pump"), as tender titles do.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from app.discovery.gem_parser import GemListing, listing_category_words
from app.ingestion.product_tagger import ProductMatcher
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import (
    PhrasePatterns,
    SearchVocabulary,
    normalize_for_search,
    phrase_patterns,
)


def other_product_patterns(
    ontologies: Sequence[Ontology], vocabulary: SearchVocabulary
) -> dict[str, PhrasePatterns]:
    """Patterns of the ontologies' `other_products` (products with no type), by phrase.

    Args:
        ontologies: The loaded ontologies.
        vocabulary: Compiled ontology code rules.

    Returns:
        Phrase -> its patterns over normalized text.
    """
    phrases = [p for o in ontologies for p in o.other_products]
    return {p: phrase_patterns([p], vocabulary) for p in phrases}


def main_name_patterns(
    ontologies: Sequence[Ontology], vocabulary: SearchVocabulary
) -> tuple[PhrasePatterns, PhrasePatterns]:
    """Patterns of the ontologies' accessory words and part words.

    Args:
        ontologies: The loaded ontologies.
        vocabulary: Compiled ontology code rules.

    Returns:
        (accessory word patterns, part word patterns) over normalized text.
    """
    accessory = phrase_patterns([w for o in ontologies for w in o.accessory_words], vocabulary)
    parts = phrase_patterns([w for o in ontologies for w in o.part_words], vocabulary)
    return accessory, parts


def synonym_words(
    ontologies: Sequence[Ontology], vocabulary: SearchVocabulary
) -> dict[str, tuple[tuple[str, ...], ...]]:
    """Normalized words of every multi-word product type synonym, by type.

    Args:
        ontologies: The loaded ontologies.
        vocabulary: Compiled ontology code rules.

    Returns:
        Product type -> word tuples ("submersible pump" -> ("submers", "pump")).
    """
    found: dict[str, tuple[tuple[str, ...], ...]] = {}
    for product in (p for o in ontologies for p in o.product_types):
        normalized = (tuple(normalize_for_search(s, vocabulary).split()) for s in product.synonyms)
        found[product.name] = tuple(w for w in normalized if len(w) > 1)
    return found


def key_pattern(
    phrase: str, gap_words: Sequence[str], vocabulary: SearchVocabulary
) -> re.Pattern[str]:
    """A phrase over normalized text, its words in order with only filler words between.

    Args:
        phrase: The key as words ("y strainer").
        gap_words: Filler words allowed between them ("type").
        vocabulary: Compiled ontology code rules.

    Returns:
        The pattern ("y type strainer" and "y strainer" match, "y water strainer" does not).
    """
    parts = [re.escape(w) for w in normalize_for_search(phrase, vocabulary).split()]
    gaps = sorted({g for w in gap_words for g in normalize_for_search(w, vocabulary).split()})
    gap = rf"(?:\s+(?:{'|'.join(re.escape(g) for g in gaps)}))*" if gaps else ""
    return re.compile(rf"(?<!\S){(gap + r'\s+').join(parts)}(?!\S)")


@dataclass(frozen=True, slots=True)
class ListingCheck:
    """What a kept listing must show."""

    matcher: ProductMatcher
    vocabulary: SearchVocabulary
    min_spec_rows: int
    other_products: Mapping[str, PhrasePatterns] = field(default_factory=dict)
    accessory_words: PhrasePatterns = ()
    part_words: PhrasePatterns = ()
    # Product type -> the normalized words of each of its multi-word synonyms.
    type_words: Mapping[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)
    # Title keys this check was given (`for_type`): products no ontology defines.
    key_types: frozenset[str] = frozenset()

    @classmethod
    def from_ontologies(
        cls,
        ontologies: Sequence[Ontology],
        vocabulary: SearchVocabulary,
        matcher: ProductMatcher,
        min_spec_rows: int,
    ) -> ListingCheck:
        """A check with every ontology word list.

        Args:
            ontologies: The loaded ontologies.
            vocabulary: Compiled ontology code rules.
            matcher: Product type matcher.
            min_spec_rows: Fewest specification rows a kept listing has.

        Returns:
            The check.
        """
        accessory, parts = main_name_patterns(ontologies, vocabulary)
        return cls(
            matcher,
            vocabulary,
            min_spec_rows,
            other_product_patterns(ontologies, vocabulary),
            accessory,
            parts,
            synonym_words(ontologies, vocabulary),
        )

    def for_type(self, product_type: str, gap_words: Sequence[str] = ()) -> ListingCheck:
        """This check, knowing `product_type` even when no ontology defines it.

        A title key ("y_strainer", see `tender/title_key.py`) has no curated synonyms, so its
        words must appear in order, with only filler words between them: "Y Type Strainer"
        names `y_strainer`, "Foot Operated Water Valve" does not name `foot_valve`.

        Args:
            product_type: An ontology type or a title key.
            gap_words: Filler words allowed between a key's words ("type").

        Returns:
            The same check for a known type, else one that also knows the key.
        """
        if product_type in self.matcher.types:
            return self
        pattern = key_pattern(product_type.replace("_", " "), gap_words, self.vocabulary)
        matcher = replace(self.matcher, types={**self.matcher.types, product_type: (pattern,)})
        return replace(
            self,
            matcher=matcher,
            type_words={**self.type_words, product_type: ()},
            key_types=self.key_types | {product_type},
        )

    def is_title_key(self, product_type: str) -> bool:
        """True for a title key (`for_type`), False for an ontology product type."""
        return product_type in self.key_types

    def qualifiers_named(
        self,
        category: str,
        product_type: str,
        title_words: frozenset[str],
        tail_words: frozenset[str] = frozenset(),
    ) -> bool:
        """True if every word a category name puts before a title key is a tender title word.

        A title key has no curated synonyms, so a qualifier the tender never wrote makes it
        another product: "Cadaverous Injector" is no diesel "INJECTOR 74 110 HP", "Toggle
        Switches" no "SWITCH FOR HAZARIODS". The word right after the key must only describe it:
        a filler or standard word ("Starter Motors for IC Engines", "Foot Valves IS 4038"), a
        code, or a title word; "Switch Disconnectors and Fuse Units" is another product.

        Args:
            category: A GeM category name.
            product_type: The title key.
            title_words: Normalized words of the tender items' titles.
            tail_words: Normalized words that may follow the key (filler, standards).

        Returns:
            Whether the category may be the item's product.
        """
        text = self._normalized(category)
        found = [m for p in self.matcher.types[product_type] if (m := p.search(text))]
        if not found:
            return False
        first = min(found, key=lambda m: m.start())
        before = [w for w in text[: first.start()].split() if not w.isdigit()]
        after = text[first.end() :].split()[:1]
        tail_ok = all(
            w in tail_words or w in title_words or any(ch.isdigit() for ch in w) or len(w) == 1
            for w in after
        )
        return tail_ok and all(w in title_words for w in before)

    def names_type(self, listing: GemListing, product_type: str) -> bool:
        """True if the title or a specification row names the product type.

        Args:
            listing: A parsed listing.
            product_type: The ontology product type looked for.

        Returns:
            Whether the type is named.
        """
        texts = [listing.title, *(f"{r.label} {r.value}" for r in listing.specs)]
        if any(self._names(normalize_for_search(t, self.vocabulary), product_type) for t in texts):
            return True
        # A title key has no synonyms: sellers name the product their own way ("Aluminium
        # Phosphide 56% Tab" in "Aluminium Phosphide Formulation (V3)"), so the listing's own
        # GeM category, read from its URL, names it too.
        category = listing_category_words(listing.url)
        return (
            self.is_title_key(product_type)
            and bool(category)
            and self.names_type_in(category, product_type)
        )

    def foreign_product(
        self, listing: GemListing, product_type: str, excludes: Sequence[str] = ()
    ) -> str | None:
        """The other product the title or model names instead of the wanted type, if any.

        Args:
            listing: A parsed listing.
            product_type: The ontology product type looked for.
            excludes: GeM look-alike phrases configured for this type.

        Returns:
            The foreign product's name, or None.
        """
        for part in (listing.title, listing.model or ""):
            other = self.foreign_in(part, product_type, excludes)
            if other:
                return other
            if self._names(self._normalized(part), product_type) and not self.names_type_in(
                part, product_type
            ):
                return f"{product_type} accessory or part"
        return None

    def foreign_in(self, text: str, product_type: str, excludes: Sequence[str] = ()) -> str | None:
        """The other product a text names instead of the wanted type, if any.

        Args:
            text: Printed text (a title, a model, a GeM category name).
            product_type: The ontology product type looked for.
            excludes: GeM look-alike phrases configured for this type.

        Returns:
            The foreign product's name, or None (no foreign name, or the wanted type is still
            named once the foreign names are taken out).
        """
        foreign = self._foreign_patterns(product_type, excludes)
        normalized = normalize_for_search(text, self.vocabulary)
        named = [n for n, pats in foreign.items() if any(p.search(normalized) for p in pats)]
        if not named:
            return None
        rest = normalized
        for pattern in (p for name in named for p in foreign[name]):
            rest = pattern.sub(" ", rest)
        return None if self._names(rest, product_type) else named[0]

    def names_type_in(self, text: str, product_type: str) -> bool:
        """True if printed text names the product type as the product itself.

        The type must be named before any accessory word ("with") and not be followed by a
        part word: "Ball Valve with Gear Operator" names a ball valve, "Gear Operator with
        Ball Valve" and "Ball Valve Repair Kit" do not.

        Args:
            text: Printed text (a GeM category name, a listing title or model).
            product_type: The ontology product type.

        Returns:
            Whether it is named.
        """
        head = self._before_accessory(self._normalized(text))
        if not self._names(head, product_type):
            return self._names_words_apart(head, product_type)
        named_at = [
            m.end() for pattern in self.matcher.types[product_type] for m in pattern.finditer(head)
        ]
        # Named only inside a list ("gate and globe valves"): no single name to look after.
        return not named_at or any(not self._part_follows(head[end:]) for end in named_at)

    def _names_words_apart(self, head: str, product_type: str) -> bool:
        """True if every word of a multi-word synonym is in `head`, apart or in another order.

        "submersible sewerage pump" names a submersible pump; the words after the last synonym
        word must not be a part word ("submersible pump cable starter").
        """
        tokens = head.split()
        for synonym in self.type_words.get(product_type, ()):
            if not set(synonym) <= set(tokens):
                continue
            last = max(i for i, token in enumerate(tokens) if token in synonym)
            if not self._part_follows(" ".join(tokens[last + 1 :])):
                return True
        return False

    def _part_follows(self, tail: str) -> bool:
        """True if normalized text after a product name holds a part word ("repair kit")."""
        return any(p.search(tail) for p in self.part_words)

    def _normalized(self, text: str) -> str:
        """Normalized (`search_text`) form of printed text."""
        return normalize_for_search(text, self.vocabulary)

    def _before_accessory(self, normalized: str) -> str:
        """The text before its first accessory word; all of it if it has none."""
        starts = [m.start() for p in self.accessory_words if (m := p.search(normalized))]
        return normalized[: min(starts)] if starts else normalized

    def rejection(
        self, listing: GemListing, product_type: str, excludes: Sequence[str] = ()
    ) -> str | None:
        """Why the listing is not kept, or None to keep it.

        Args:
            listing: A parsed listing.
            product_type: The ontology product type looked for.
            excludes: GeM look-alike phrases configured for this type.

        Returns:
            A short reason, or None.
        """
        if len(listing.specs) < self.min_spec_rows:
            return f"only {len(listing.specs)} specification rows (need {self.min_spec_rows})"
        other = self.foreign_product(listing, product_type, excludes)
        if other:
            return f"names {other.replace('_', ' ')}, not {product_type.replace('_', ' ')}"
        if not self.names_type(listing, product_type):
            return f"does not name {product_type.replace('_', ' ')} (another product)"
        return None

    def _names(self, search_text: str, product_type: str) -> bool:
        """True if normalized text names the product type."""
        return bool(self.matcher.types_in(search_text, frozenset({product_type})))

    def _foreign_patterns(
        self, product_type: str, excludes: Sequence[str]
    ) -> dict[str, PhrasePatterns]:
        """Every product name that is not the wanted type, with its patterns."""
        foreign: dict[str, PhrasePatterns] = {
            name: pats for name, pats in self.matcher.types.items() if name != product_type
        }
        # A product with no type may be the wanted key itself, or part of it ("strainer" in
        # "y_strainer"): neither is foreign to it.
        own = set(product_type.split("_"))
        foreign |= {p: pats for p, pats in self.other_products.items() if not set(p.split()) <= own}
        foreign |= {e: phrase_patterns([e], self.vocabulary) for e in excludes}
        return foreign
