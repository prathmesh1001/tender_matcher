"""Tender pipeline: schedule tables -> line items -> atomic requirements.

The ontology is optional. For each item the ontology whose product types name the item
title is used (unit spellings, canonical attributes); with none, requirements are still
split and parsed, with units as printed and no canonical attribute.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import Settings
from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import ChunkDraft
from app.schemas.requirements import LineItem, Requirement
from app.tender.line_items import ItemColumnOptions, line_items
from app.tender.normalizer import words
from app.tender.splitter import SplitOptions, item_requirements


@dataclass(frozen=True, slots=True)
class RequirementOptions:
    """Everything the tender pipeline needs (built once from settings)."""

    columns: ItemColumnOptions
    split: SplitOptions

    @classmethod
    def from_settings(cls, settings: Settings) -> RequirementOptions:
        """Build options from application settings."""
        return cls(
            columns=ItemColumnOptions.from_settings(settings),
            split=SplitOptions(
                label_max_words=settings.TENDER_LABEL_MAX_WORDS,
                enum_max_words=settings.TENDER_ENUM_MAX_WORDS,
            ),
        )


@dataclass(frozen=True, slots=True)
class ItemRequirements:
    """One line item, the ontology category used for it (if any) and its requirements."""

    item: LineItem
    category: str | None
    requirements: list[Requirement]


def _names_product(title_words: list[str], ontology: Ontology) -> bool:
    """True if one of the ontology's product-type synonyms is a word run of the title."""
    for product in ontology.product_types:
        for synonym in product.synonyms:
            syn = words(synonym)
            if any(title_words[i : i + len(syn)] == syn for i in range(len(title_words))):
                return True
    return False


def ontology_for(title: str, ontologies: Sequence[Ontology]) -> Ontology | None:
    """The first ontology whose product types the item title names, or None."""
    title_words = words(fold_text(title))
    return next((o for o in ontologies if _names_product(title_words, o)), None)


def tender_requirements(
    chunks: Sequence[ChunkDraft],
    document: str,
    options: RequirementOptions,
    ontologies: Sequence[Ontology] = (),
) -> list[ItemRequirements]:
    """Line items of one tender file and their requirements.

    Args:
        chunks: The file's chunks.
        document: File name, for citations.
        options: Column labels and splitting thresholds.
        ontologies: Loaded ontologies; empty runs without one.

    Returns:
        One entry per line item, in document order.
    """
    out: list[ItemRequirements] = []
    for item in line_items(chunks, document, options.columns):
        ontology = ontology_for(item.title, ontologies)
        out.append(
            ItemRequirements(
                item=item,
                category=ontology.category if ontology else None,
                requirements=item_requirements(item, ontology, options.split),
            )
        )
    return out
