"""Which file a tender's line items come from (T1, T10), and everything left over.

Tenders repeat their items across files, and terms tables look like schedules, so reading every
file gives duplicates and non-items. The order, first that gives items:

1. the item list (`boq_items.csv`), unless a spec schedule has the same item numbers and says
   more about them (some lists only repeat the titles);
2. schedule tables of specification files, never the ATC or the bid itself;
3. the bid's "Item Category" row as one item, with each specification chunk read for its
   requirements, because then nothing tells which line belongs to which product.

Anything not used as an item becomes a general requirement. An item with no description keeps
its title as its one requirement, so it is still reported.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, Operator
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.tender.general import english_part, general_requirements, row_text
from app.tender.line_items import header_key
from app.tender.pipeline import (
    ItemRequirements,
    RequirementOptions,
    item_entry,
    tender_general_requirements,
    tender_requirements,
)
from app.tender.splitter import leaves

ITEM_LABEL: Final = "ITEM"
_CATEGORY_ITEM_NO: Final = "1"
_NOT_A_FILE_NAME_CHAR: Final = re.compile(r"[^A-Za-z0-9]+")


class ItemSource(StrEnum):
    """Where a tender's line items came from."""

    ITEM_LIST = "item_list"
    SPEC_SCHEDULE = "spec_schedule"
    ITEM_CATEGORY = "item_category"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class TenderFile:
    """One processed tender file."""

    name: str
    role: str
    chunks: Sequence[ChunkDraft]


@dataclass(frozen=True, slots=True)
class TenderRequirements:
    """All of one tender: its items, everything else, and which rule gave the items."""

    items: list[ItemRequirements]
    general: list[Requirement]
    source: ItemSource


def _atomic_count(entries: Sequence[ItemRequirements]) -> int:
    return sum(len(leaves(e.requirements)) for e in entries)


def _renumbered(entry: ItemRequirements, item_no: str) -> ItemRequirements:
    """The item under another number, its requirement ids following."""
    old = entry.item.item_no

    def moved(req_id: str | None) -> str | None:
        return f"{item_no}{req_id[len(old):]}" if req_id else None

    requirements = [
        r.model_copy(
            update={"id": moved(r.id), "parent_id": moved(r.parent_id), "item_no": item_no}
        )
        for r in entry.requirements
    ]
    return ItemRequirements(
        item=entry.item.model_copy(update={"item_no": item_no}),
        category=entry.category,
        requirements=requirements,
        product_type=entry.product_type,
    )


def _unique_numbers(entries: Sequence[ItemRequirements]) -> list[ItemRequirements]:
    """Items keep their printed number; a number used twice (two files) is prefixed by the file."""
    counts = Counter(e.item.item_no for e in entries)
    out: list[ItemRequirements] = []
    for entry in entries:
        if counts[entry.item.item_no] == 1:
            out.append(entry)
            continue
        stem = _NOT_A_FILE_NAME_CHAR.sub("_", entry.item.document.rsplit(".", 1)[0])
        out.append(_renumbered(entry, f"{stem}.{entry.item.item_no}"))
    return out


def _with_title(entry: ItemRequirements) -> ItemRequirements:
    """An item without any requirement keeps its title as one (it holds the spec)."""
    if entry.requirements:
        return entry
    item = entry.item
    title = Requirement(
        id=f"{item.item_no}.1",
        item_no=item.item_no,
        position=0,
        label=ITEM_LABEL,
        product_type=entry.product_type,
        mandatory=True,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text=item.title,
        raw_text=item.title,
        document=item.document,
        page_number=item.page_number,
    )
    return ItemRequirements(item, entry.category, [title], entry.product_type)


def _schedules(
    files: Sequence[TenderFile],
    roles: Sequence[str],
    options: RequirementOptions,
    ontologies: Sequence[Ontology],
) -> dict[str, list[ItemRequirements]]:
    """Line items per file, for the files with these roles that have any."""
    found: dict[str, list[ItemRequirements]] = {}
    for file in files:
        if file.role in roles:
            entries = tender_requirements(file.chunks, file.name, options, ontologies)
            if entries:
                found[file.name] = entries
    return found


def _better_spec(
    listed: Sequence[ItemRequirements], specs: dict[str, list[ItemRequirements]]
) -> bool:
    """True if the spec schedules cover the list's item numbers and say more about them."""
    in_specs = [e for entries in specs.values() for e in entries]
    numbers = {e.item.item_no for e in in_specs}
    covered = {e.item.item_no for e in listed} <= numbers
    return covered and _atomic_count(in_specs) > _atomic_count(listed)


def _chosen_schedule(
    files: Sequence[TenderFile], options: RequirementOptions, ontologies: Sequence[Ontology]
) -> tuple[dict[str, list[ItemRequirements]], ItemSource]:
    """The files the items come from, by the order in the module docstring."""
    lists = _schedules(files, options.item_list_roles, options, ontologies)
    specs = _schedules(files, options.spec_roles, options, ontologies)
    if lists:
        name, listed = next(iter(lists.items()))
        if not _better_spec(listed, specs):
            return {name: listed}, ItemSource.ITEM_LIST
    if specs:
        return specs, ItemSource.SPEC_SCHEDULE
    return {}, ItemSource.NONE


def item_category(chunks: Sequence[ChunkDraft], labels: Sequence[str]) -> tuple[str, int] | None:
    """The bid's item category string and its page, from its label/value row.

    Args:
        chunks: The bid file's chunks.
        labels: Folded label words of the row ("item category").

    Returns:
        The value and page, or None if the bid has no such row.
    """
    for chunk in chunks:
        if chunk.chunk_type is not ChunkType.TABLE_ROW:
            continue
        label, value = row_text(chunk)
        if label and value and header_key(label) in labels:
            return value, chunk.page_number
    return None


def _chunk_lines(chunk: ChunkDraft) -> list[str]:
    """A specification chunk's lines to split into requirements (English part only)."""
    if chunk.chunk_type is ChunkType.TABLE_ROW:
        label, value = row_text(chunk)
        return [f"{label} : {value}" if label else value]
    return [line for line in (english_part(x) for x in chunk.raw_text.splitlines()) if line]


def _category_entry(
    files: Sequence[TenderFile], options: RequirementOptions, ontologies: Sequence[Ontology]
) -> tuple[ItemRequirements, set[str]] | None:
    """One item from the bid's item category; its requirements cite their own file and page."""
    bid = next((f for f in files if f.role == options.bid_role), None)
    found = item_category(bid.chunks, options.item_category_labels) if bid else None
    if bid is None or found is None:
        return None
    title, page = found
    item = LineItem(
        item_no=_CATEGORY_ITEM_NO,
        title=title,
        description_lines=[],
        document=bid.name,
        page_number=page,
    )
    base = item_entry(item, options, ontologies)
    requirements: list[Requirement] = []
    used: set[str] = set()
    for file in (f for f in files if f.role in options.spec_roles):
        used.add(file.name)
        for index, chunk in enumerate(file.chunks):
            part = item.model_copy(
                update={
                    "description_lines": _chunk_lines(chunk),
                    "document": file.name,
                    "page_number": chunk.page_number,
                    "item_no": f"{_CATEGORY_ITEM_NO}.{file.name}.{index}",
                }
            )
            found_here = item_entry(part, options, ontologies).requirements
            requirements.extend(r.model_copy(update={"item_no": item.item_no}) for r in found_here)
    entry = ItemRequirements(item, base.category, requirements, base.product_type)
    return entry, used


def _general(
    files: Sequence[TenderFile], used: set[str], schedule: set[str], options: RequirementOptions
) -> list[Requirement]:
    """General requirements of every file not fully used as item text."""
    out: list[Requirement] = []
    for file in files:
        if file.name in used:
            continue
        if file.name in schedule:
            out.extend(tender_general_requirements(file.chunks, file.name, options))
        else:
            out.extend(general_requirements(file.chunks, file.name, options.classifier))
    return out


def parse_tender_files(
    files: Sequence[TenderFile], options: RequirementOptions, ontologies: Sequence[Ontology] = ()
) -> TenderRequirements:
    """Items and general requirements of one tender.

    Args:
        files: The tender's processed files.
        options: Column labels, roles, thresholds and classifier.
        ontologies: Loaded ontologies; empty runs without one.

    Returns:
        Everything the tender asks for, and which rule gave the items.
    """
    chosen, source = _chosen_schedule(files, options, ontologies)
    if chosen:
        items = _unique_numbers([e for entries in chosen.values() for e in entries])
        general = _general(files, set(), set(chosen), options)
        return TenderRequirements([_with_title(e) for e in items], general, source)
    category = _category_entry(files, options, ontologies)
    if category is None:
        return TenderRequirements([], _general(files, set(), set(), options), ItemSource.NONE)
    entry, used = category
    general = _general(files, used, set(), options)
    return TenderRequirements([_with_title(entry)], general, ItemSource.ITEM_CATEGORY)
