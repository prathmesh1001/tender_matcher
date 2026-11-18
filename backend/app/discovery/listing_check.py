"""Whether a GeM listing may be kept as evidence for a product type.

A listing is kept only if it is about that product and says enough: its title or one of its
specification rows names the product type (ontology synonyms, matched on normalized text like
every other product check), and it has at least the configured number of rows. GeM category
pages also list neighbouring products, so the category alone proves nothing.
"""

from dataclasses import dataclass

from app.discovery.gem_parser import GemListing
from app.ingestion.product_tagger import ProductMatcher
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search


@dataclass(frozen=True, slots=True)
class ListingCheck:
    """What a kept listing must show."""

    matcher: ProductMatcher
    vocabulary: SearchVocabulary
    min_spec_rows: int

    def names_type(self, listing: GemListing, product_type: str) -> bool:
        """True if the title or a specification row names the product type.

        Args:
            listing: A parsed listing.
            product_type: The ontology product type looked for.

        Returns:
            Whether the type is named.
        """
        wanted = frozenset({product_type})
        texts = [listing.title, *(f"{r.label} {r.value}" for r in listing.specs)]
        return any(
            self.matcher.types_in(normalize_for_search(t, self.vocabulary), wanted) for t in texts
        )

    def rejection(self, listing: GemListing, product_type: str) -> str | None:
        """Why the listing is not kept, or None to keep it.

        Args:
            listing: A parsed listing.
            product_type: The ontology product type looked for.

        Returns:
            A short reason, or None.
        """
        if len(listing.specs) < self.min_spec_rows:
            return f"only {len(listing.specs)} specification rows (need {self.min_spec_rows})"
        if not self.names_type(listing, product_type):
            return f"does not name {product_type.replace('_', ' ')} (another product)"
        return None
