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
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.discovery.gem_parser import GemListing
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


@dataclass(frozen=True, slots=True)
class ListingCheck:
    """What a kept listing must show."""

    matcher: ProductMatcher
    vocabulary: SearchVocabulary
    min_spec_rows: int
    other_products: Mapping[str, PhrasePatterns] = field(default_factory=dict)

    def names_type(self, listing: GemListing, product_type: str) -> bool:
        """True if the title or a specification row names the product type.

        Args:
            listing: A parsed listing.
            product_type: The ontology product type looked for.

        Returns:
            Whether the type is named.
        """
        texts = [listing.title, *(f"{r.label} {r.value}" for r in listing.specs)]
        return any(
            self._names(normalize_for_search(t, self.vocabulary), product_type) for t in texts
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
        foreign = self._foreign_patterns(product_type, excludes)
        for part in (listing.title, listing.model or ""):
            text = normalize_for_search(part, self.vocabulary)
            named = [name for name, pats in foreign.items() if any(p.search(text) for p in pats)]
            if not named:
                continue
            rest = text
            for pattern in (p for name in named for p in foreign[name]):
                rest = pattern.sub(" ", rest)
            if not self._names(rest, product_type):
                return named[0]
        return None

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
        foreign |= dict(self.other_products)
        foreign |= {e: phrase_patterns([e], self.vocabulary) for e in excludes}
        return foreign
