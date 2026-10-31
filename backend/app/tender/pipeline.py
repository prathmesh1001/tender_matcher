"""Tender pipeline: schedule tables -> line items -> atomic requirements, plus everything else.

The ontology is optional. For each item the ontology whose product types name the item
title is used (unit spellings, canonical attributes); with none, requirements are still
split and parsed, with units as printed and no canonical attribute. Every requirement is
classified (type, category, evidence source, mandatory, named make), and chunks outside the
schedule become general requirements, so nothing a tender asks for is silently ignored.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from app.core.config import Settings
from app.matching.values import quantities
from app.ontology.schema import Ontology, ProductType
from app.ontology.units import canonical_number
from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import Operator
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.tender.classifier import RequirementClassifier
from app.tender.general import general_requirements
from app.tender.line_items import ItemColumnOptions, is_schedule_row, line_items
from app.tender.link_fetcher import BID_ROLE
from app.tender.normalizer import stems, title_size, words
from app.tender.splitter import SIZE_LABEL, SplitOptions, item_requirements

SIZE_DIFFERS_FROM_TITLE: Final = "size_differs_from_title"
MAKE_LABEL: Final = "MAKE"


@dataclass(frozen=True, slots=True)
class RequirementOptions:
    """Everything the tender pipeline needs (built once from settings)."""

    columns: ItemColumnOptions
    split: SplitOptions
    classifier: RequirementClassifier
    designation_prefixes: tuple[str, ...]
    item_list_roles: tuple[str, ...]
    spec_roles: tuple[str, ...]
    bid_role: str
    item_category_labels: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings: Settings) -> RequirementOptions:
        """Build options from application settings."""
        return cls(
            columns=ItemColumnOptions.from_settings(settings),
            split=SplitOptions(
                label_max_words=settings.TENDER_LABEL_MAX_WORDS,
                enum_max_words=settings.TENDER_ENUM_MAX_WORDS,
                inline_key_max_words=settings.TENDER_INLINE_KEY_MAX_WORDS,
            ),
            classifier=RequirementClassifier.from_settings(settings),
            designation_prefixes=tuple(settings.COMPARATOR_DESIGNATION_PREFIXES),
            item_list_roles=tuple(settings.TENDER_ITEM_LIST_ROLES),
            spec_roles=tuple(settings.TENDER_SPEC_ROLES),
            bid_role=BID_ROLE,
            item_category_labels=tuple(settings.TENDER_ITEM_CATEGORY_LABELS),
        )


@dataclass(frozen=True, slots=True)
class ItemRequirements:
    """One line item, the ontology category used for it (if any) and its requirements."""

    item: LineItem
    category: str | None
    requirements: list[Requirement]
    product_type: str | None = None


def _named(title_words: list[str], product: ProductType) -> bool:
    """True if one of the product's synonyms is a word run of the title."""
    for synonym in product.synonyms:
        syn = words(synonym)
        if any(title_words[i : i + len(syn)] == syn for i in range(len(title_words))):
            return True
    return False


def product_types_named(title: str, ontology: Ontology) -> list[str]:
    """Product types a title names: as a phrase, else with its words in any order.

    "CENTRIFUGAL HORIZONTAL PUMP" names `centrifugal_pump`. The exact phrase goes first, so
    a title naming a phrase is not also read as every type sharing its words.

    Args:
        title: The item title.
        ontology: Its product types and synonyms.

    Returns:
        The names of the types found.
    """
    title_words = words(fold_text(title))
    exact = [p.name for p in ontology.product_types if _named(title_words, p)]
    if exact:
        return exact
    in_title = set(stems(title))
    return [
        p.name
        for p in ontology.product_types
        if any(set(stems(synonym)) <= in_title for synonym in p.synonyms)
    ]


def ontology_for(title: str, ontologies: Sequence[Ontology]) -> Ontology | None:
    """The first ontology whose product types the item title names, or None."""
    return next((o for o in ontologies if product_types_named(title, o)), None)


def product_type_for(title: str, ontology: Ontology | None) -> str | None:
    """The one product type the item title names; None for none or several (never guessed)."""
    if ontology is None:
        return None
    found = product_types_named(title, ontology)
    return found[0] if len(found) == 1 else None


def size_conflict(
    requirement: Requirement, title: str, ontology: Ontology | None, prefixes: Sequence[str]
) -> bool:
    """True if a size requirement states another size than the item title (a sign of item bleed).

    Args:
        requirement: An atomic requirement.
        title: The item title.
        ontology: Says which attribute is a pipe size and converts units.
        prefixes: Designations written before their number ("dn").

    Returns:
        Whether both state a size and they differ; False if either states none.
    """
    if ontology is None or requirement.attribute is None or requirement.value_num is None:
        return False
    attribute = ontology.attribute(requirement.attribute)
    if attribute is None or not attribute.nominal_pipe_size:
        return False
    wanted = canonical_number(requirement.value_num, requirement.unit, attribute, ontology)
    in_title = {
        canonical_number(q.high, q.unit, attribute, ontology) for q in quantities(title, prefixes)
    }
    in_title.discard(None)
    return wanted is not None and bool(in_title) and wanted not in in_title


def _classified(
    requirement: Requirement,
    entry: LineItem,
    ontology: Ontology | None,
    product_type: str | None,
    options: RequirementOptions,
) -> Requirement:
    """One item requirement with its classification, product type and consistency flags."""
    text = (
        f"{requirement.label}: {requirement.value_text}"
        if requirement.label
        else (requirement.value_text)
    )
    line = options.classifier.classify(text, is_item=True)
    conflict = size_conflict(requirement, entry.title, ontology, options.designation_prefixes)
    return requirement.model_copy(
        update={
            "req_type": line.req_type,
            "category": line.category,
            "evidence": line.evidence,
            "mandatory": line.mandatory,
            "named_make": line.named_make,
            "single_firm": line.single_firm,
            "acceptable_makes": list(line.acceptable_makes),
            "or_equivalent": line.or_equivalent,
            "product_type": product_type,
            "flags": [SIZE_DIFFERS_FROM_TITLE] if conflict else [],
        }
    )


def _header_make(
    entry: LineItem, position: int, product_type: str | None, options: RequirementOptions
) -> Requirement | None:
    """A named-make requirement from the description column's header (T9), if it names one."""
    make = options.classifier.named_make(entry.description_header)
    if make is None:
        return None
    return Requirement(
        id=f"{entry.item_no}.make",
        item_no=entry.item_no,
        position=position,
        label=MAKE_LABEL,
        category=options.classifier.rules.item_default_category,
        product_type=product_type,
        mandatory=True,
        named_make=make,
        single_firm=True,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text=make,
        raw_text=entry.description_header,
        document=entry.document,
        page_number=entry.page_number,
    )


def item_entry(
    entry: LineItem, options: RequirementOptions, ontologies: Sequence[Ontology]
) -> ItemRequirements:
    """One line item with its classified requirements."""
    ontology = ontology_for(entry.title, ontologies)
    product_type = product_type_for(entry.title, ontology)
    requirements = [
        _classified(r, entry, ontology, product_type, options)
        for r in item_requirements(entry, ontology, options.split)
    ]
    make = _header_make(entry, len(requirements), product_type, options)
    return ItemRequirements(
        item=entry,
        category=ontology.category if ontology else None,
        requirements=[*requirements, make] if make else requirements,
        product_type=product_type,
    )


def with_title_size(
    entry: ItemRequirements, options: RequirementOptions, ontologies: Sequence[Ontology]
) -> ItemRequirements:
    """An item with its title's size as a requirement, if the title is all that states one.

    Only for an item of a known product type: a size in any other title may mean anything.

    Args:
        entry: The item and its requirements.
        options: Splitting thresholds and classifier.
        ontologies: Loaded ontologies.

    Returns:
        The item, with one more requirement if none states the size and the title states one.
    """
    ontology = ontology_for(entry.item.title, ontologies)
    size = title_size(entry.item.title)
    if ontology is None or entry.product_type is None or size is None:
        return entry
    for requirement in entry.requirements:
        attribute = ontology.attribute(requirement.attribute) if requirement.attribute else None
        if attribute is not None and attribute.nominal_pipe_size:
            return entry
    item_no = entry.item.item_no
    line = entry.item.model_copy(
        update={"item_no": f"{item_no}.title", "description_lines": [f"{SIZE_LABEL} : {size}"]}
    )
    added = [
        r.model_copy(update={"item_no": item_no, "raw_text": entry.item.title})
        for r in item_entry(line, options, ontologies).requirements
    ]
    return replace(entry, requirements=[*entry.requirements, *added])


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
        options: Column labels, splitting thresholds and classifier.
        ontologies: Loaded ontologies; empty runs without one.

    Returns:
        One entry per line item, in document order.
    """
    return [
        item_entry(entry, options, ontologies)
        for entry in line_items(chunks, document, options.columns)
    ]


def tender_general_requirements(
    chunks: Sequence[ChunkDraft], document: str, options: RequirementOptions
) -> list[Requirement]:
    """Requirements of one tender file outside its item schedule.

    Args:
        chunks: The file's chunks.
        document: File name, for ids and citations.
        options: Column labels and classifier.

    Returns:
        One requirement per readable line that is not a schedule row.
    """
    rest = [c for c in chunks if not is_schedule_row(c, options.columns)]
    return general_requirements(rest, document, options.classifier)
