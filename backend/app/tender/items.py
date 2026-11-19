"""Which file a tender's line items come from (T1, T10), and everything left over.

Tenders repeat their items across files, and terms tables look like schedules, so reading every
file gives duplicates and non-items. The order, first that gives items:

1. the item list (`boq_items.csv`), unless a spec schedule has the same item numbers and says
   more about them (some lists only repeat the titles);
2. schedule tables of specification files, never the ATC or the bid itself;
3. the bid's "Item Category" row as one item, with each specification chunk read for its
   requirements, because then nothing tells which line belongs to which product.

A tender with a single item left nearly empty gets the rows of its GeM category specification
table ("Specification Name: Head; Bid Requirement (Allowed Values): 14.0 (meter)") as that item's
requirements: with one item, they can only be about it. Other general lines stay general.

Specification text cut at item boundaries (`segments`) then joins its items, and whatever is not
used becomes a general requirement. An item with no description keeps its title as its one
requirement, so it is still reported.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.ontology.schema import Ontology
from app.retrieval.text_normalizer import fold_text
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, Operator, RequirementType
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.tender.code_rows import codes_in, find_code_row
from app.tender.general import english_part, general_requirements, row_text
from app.tender.line_items import header_key
from app.tender.name_segments import NameRules, segment_by_name
from app.tender.normalizer import title_size
from app.tender.pipeline import (
    ItemRequirements,
    RequirementOptions,
    item_entry,
    ontology_for,
    tender_general_requirements,
    tender_requirements,
    with_title_size,
)
from app.tender.quality import TITLE_PART_SEPARATOR, title_merges_products
from app.tender.segments import (
    FileSegments,
    Segment,
    SegmentRules,
    item_clause,
    segment_file,
)
from app.tender.splitter import leaves

ITEM_LABEL: Final = "ITEM"
_CATEGORY_ITEM_NO: Final = "1"
_TRAILING_NUMBER: Final = re.compile(r"(\d+)$")
_NOT_A_FILE_NAME_CHAR: Final = re.compile(r"[^A-Za-z0-9]+")
_DEVANAGARI: Final = re.compile("[ऀ-ॿ]")
# GeM's quality level after a category title: "Butterfly Valves (V2) as per IS 13095 (Q2)".
_QUALITY_TAG: Final = re.compile(r"\s*\(\s*q\d+\s*\)\s*$", re.IGNORECASE)
# What follows the title on an item's block line: "( 70 pieces )".
_QUANTITY: Final = re.compile(r"\s*\(\s*(?P<qty>\d+(?:\.\d+)?)\s+(?P<unit>[a-z][a-z. ]*)\)")


class ItemSource(StrEnum):
    """Where a tender's line items came from."""

    ITEM_LIST = "item_list"
    SPEC_SCHEDULE = "spec_schedule"
    ITEM_CATEGORY = "item_category"
    NONE = "none"


# Flag on a requirement whose value a scan lost ("%" for 1/2" or 3/4").
_UNREADABLE_FLAG: Final = "value not readable in the scan: check the document"


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
                found[file.name] = fold_datasheets(entries, options, ontologies)
    return found


def _number_runs(entries: Sequence[ItemRequirements]) -> list[list[ItemRequirements]]:
    """Entries split where the item numbering starts again ("1, 2, 3" then "1, 2, ... 31")."""
    runs: list[list[ItemRequirements]] = []
    last = None
    for entry in entries:
        found = _TRAILING_NUMBER.search(entry.item.item_no)
        number = int(found.group(1)) if found else None
        if not runs or number is None or last is None or number <= last:
            runs.append([])
        runs[-1].append(entry)
        last = number
    return runs


def _is_datasheet_row(entry: ItemRequirements, label_max_words: int) -> bool:
    """A numbered row that is a label and its value ("Size" | "1'', 25mm"), not a product."""
    title = entry.item.title.strip()
    lines = [ln.strip() for ln in entry.item.description_lines if ln.strip()]
    return bool(lines) and lines[0] == title and len(title.split()) <= label_max_words


def _datasheet_line(entry: ItemRequirements) -> str:
    """A datasheet row as one "label : value" line."""
    lines = [ln.strip() for ln in entry.item.description_lines if ln.strip()]
    return f"{entry.item.title.strip()} : {' '.join(lines[1:])}"


def fold_datasheets(
    entries: Sequence[ItemRequirements],
    options: RequirementOptions,
    ontologies: Sequence[Ontology],
) -> list[ItemRequirements]:
    """Each item's datasheet folded into that item (8063368).

    A spec file can list its items ("1 Supply of pneumatic control valve ...", "2 ...") and then
    one numbered datasheet per item ("1 Size | 1''", "2 Fluid service | Steam", ...). Read as a
    schedule, every datasheet row becomes an item. When every numbering run after the first
    holds only label rows and there are as many such runs as items in the first, run i is
    item i's datasheet. Anything else is left as it was (and the extraction check flags it).

    Args:
        entries: One file's line items in document order.
        options: Label length, splitting thresholds and classifier.
        ontologies: Loaded ontologies.

    Returns:
        The items, with their datasheet rows as requirements.
    """
    runs = _number_runs(entries)
    products, sheets = runs[0], runs[1:]
    limit = options.split.label_max_words
    if len(sheets) != len(products) or not all(
        _is_datasheet_row(e, limit) for sheet in sheets for e in sheet
    ):
        return list(entries)
    out: list[ItemRequirements] = []
    for product, sheet in zip(products, sheets, strict=True):
        lines = [*product.item.description_lines, *(_datasheet_line(e) for e in sheet)]
        item = product.item.model_copy(update={"description_lines": lines})
        out.append(item_entry(item, options, ontologies))
    return out


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
    requirements = list(base.requirements)
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


def _without_repeats(
    new: Sequence[Requirement], existing: Sequence[Requirement]
) -> list[Requirement]:
    """New requirements not already stated (same label and value, or stated twice in `new`).

    A parent stays only if a child of it stays.
    """

    def key(requirement: Requirement) -> tuple[str, str]:
        value = fold_text(requirement.value_text).rstrip(" ,.;")
        return fold_text(requirement.label or ""), value

    seen = {key(r) for r in leaves(existing)}
    kept_children: set[str] = set()
    kept_leaves: set[str] = set()
    for leaf in leaves(new):
        if key(leaf) not in seen:
            seen.add(key(leaf))
            kept_leaves.add(leaf.id)
            if leaf.parent_id:
                kept_children.add(leaf.parent_id)
    return [r for r in new if r.id in kept_leaves or r.id in kept_children]


def _once(entry: ItemRequirements) -> ItemRequirements:
    """The item with each requirement stated once."""
    return ItemRequirements(
        entry.item,
        entry.category,
        _without_repeats(entry.requirements, []),
        entry.product_type,
    )


def _segment_requirements(
    entry: ItemRequirements,
    segment: Segment,
    tag: str,
    document: str,
    context: tuple[RequirementOptions, Sequence[Ontology], SegmentRules],
) -> list[Requirement]:
    """One segment's requirements for one item, each citing the page it is printed on."""
    options, ontologies, rules = context
    item_no = entry.item.item_no
    by_page: dict[int, list[str]] = {}
    unreadable = {line.text for line in segment.lines if line.unreadable}
    for line in segment.lines:
        text = item_clause(line.text, item_no, rules)
        if text is not None:
            by_page.setdefault(line.page_number, []).append(text)
    found: list[Requirement] = []
    for page, lines in by_page.items():
        part = entry.item.model_copy(
            update={
                "item_no": f"{item_no}.{tag}.p{page}",
                "description_lines": lines,
                "description_header": "",
                "document": document,
                "page_number": page,
            }
        )
        found.extend(
            (
                _unreadable(r)
                if r.raw_text in unreadable
                else r.model_copy(update={"item_no": item_no})
            )
            for r in item_entry(part, options, ontologies).requirements
        )
    return [r.model_copy(update={"item_no": item_no}) for r in found]


def _unreadable(requirement: Requirement) -> Requirement:
    """A value the scan lost: kept for review, never compared (its number would be a guess)."""
    return requirement.model_copy(
        update={
            "req_type": RequirementType.VAGUE,
            "flags": [*requirement.flags, _UNREADABLE_FLAG],
        }
    )


def _file_segments(
    file: TenderFile,
    titles: Mapping[str, str],
    rules: SegmentRules,
    name_rules: NameRules | None,
) -> FileSegments:
    """Cut a spec file at item numbers; if it prints none, at blocks headed by item names."""
    found = segment_file(file.chunks, titles.keys(), rules)
    if found.segments or name_rules is None:
        return found
    return segment_by_name(file.chunks, titles, name_rules)


def _attach_segments(
    entries: Sequence[ItemRequirements],
    files: Sequence[TenderFile],
    options: RequirementOptions,
    ontologies: Sequence[Ontology],
) -> tuple[list[ItemRequirements], dict[str, set[int]]]:
    """Add specification text cut at item boundaries to its items.

    Items sharing a number (two files) are left alone: nothing says which one a line is about.

    Returns:
        The items, and per file the chunks now held by items.
    """
    by_number = {e.item.item_no: e for e in entries}
    if len(by_number) != len(entries):
        return list(entries), {}
    rules = SegmentRules.from_rules(options.classifier.rules)
    name_rules = NameRules.from_rules(options.classifier.rules)
    titles = {number: e.item.title for number, e in by_number.items()}
    added: dict[str, list[Requirement]] = {}
    used: dict[str, set[int]] = {}
    for file in (f for f in files if f.role in options.spec_roles):
        found = _file_segments(file, titles, rules, name_rules)
        used[file.name] = found.used_chunks
        for index, segment in enumerate(found.segments, start=1):
            for number in segment.item_nos:
                context = (options, ontologies, rules)
                requirements = _segment_requirements(
                    by_number[number], segment, f"s{index}", file.name, context
                )
                held = [*by_number[number].requirements, *added.get(number, [])]
                added.setdefault(number, []).extend(_without_repeats(requirements, held))
    merged = [
        ItemRequirements(
            e.item, e.category, [*e.requirements, *added.get(e.item.item_no, [])], e.product_type
        )
        for e in entries
    ]
    return merged, used


def _general(
    files: Sequence[TenderFile],
    used: set[str],
    schedule: set[str],
    options: RequirementOptions,
    held: Mapping[str, set[int]] | None = None,
) -> list[Requirement]:
    """General requirements of every file not fully used as item text."""
    out: list[Requirement] = []
    for file in files:
        if file.name in used:
            continue
        taken = (held or {}).get(file.name, set())
        chunks = [c for i, c in enumerate(file.chunks) if i not in taken]
        if file.name in schedule:
            out.extend(tender_general_requirements(chunks, file.name, options))
        else:
            out.extend(
                general_requirements(
                    chunks, file.name, options.classifier, options.spec_row_labels[0]
                )
            )
    return out


def spec_row_line(text: str, labels: tuple[str, str]) -> str | None:
    """A GeM category specification row as "name : value", or None for any other line.

    Args:
        text: A general requirement's printed text.
        labels: The row's name label and value label.

    Returns:
        e.g. "Head : 14.0 (meter)".
    """
    name_label, value_label = labels
    if not name_label or not value_label:
        return None
    pattern = re.compile(
        rf"{re.escape(name_label)}\s*:\s*(?P<name>[^;]+);.*?{re.escape(value_label)}\s*:\s*"
        r"(?P<value>.+)$",
        re.IGNORECASE | re.DOTALL,
    )
    # A row whose value header kept only its Hindi part at a page break ("बड के िलए: IS 13095"):
    # the one field after the name is the value.
    headless = re.compile(
        rf"{re.escape(name_label)}\s*:\s*(?P<name>[^;]+);[^:;]*:\s*(?P<value>[^;]+)$",
        re.IGNORECASE | re.DOTALL,
    )
    found = pattern.search(text) or headless.search(text)
    if found is None:
        return None
    name, value = found["name"].strip(), " ".join(found["value"].split())
    # The table's own header rows repeat the labels (with their Hindi text): not requirements.
    if _DEVANAGARI.search(name) or _DEVANAGARI.search(value):
        return None
    if value.lower() in value_label.lower():
        return None
    # "Capacity, liter/hour" is one name; a comma would split it into two requirements.
    return f"{name.replace(',', '')} : {value}"


@dataclass(frozen=True, slots=True)
class _SpecBlock:
    """One item's GeM specification rows, after its "<title> ( 70 pieces )" line if printed."""

    header: Requirement | None
    quantity: str | None
    unit: str | None
    rows: list[tuple[Requirement, str]]


def quantity_header(text: str, title: str) -> tuple[str, str] | None:
    """Quantity and unit if a bid line opens one item's block ("<title> ( 70 pieces )").

    GeM prints each item of an item category as its title (without the "(Q2)" quality tag)
    and its quantity, then that item's specification table.

    Args:
        text: A general requirement's printed text.
        title: The item category title.

    Returns:
        ("70", "pieces"), or None if the line opens no block.
    """
    name = fold_text(_QUALITY_TAG.sub("", title)).strip()
    first = fold_text(text.splitlines()[0] if text else "").strip()
    if not name or not first.startswith(name):
        return None
    found = _QUANTITY.match(first[len(name) :])
    return (found["qty"], found["unit"].strip()) if found else None


def _spec_blocks(
    general: Sequence[Requirement], title: str, labels: tuple[str, str]
) -> list[_SpecBlock]:
    """Specification rows grouped by the quantity line printed before them, in bid order."""
    blocks: list[_SpecBlock] = [_SpecBlock(None, None, None, [])]
    for requirement in general:
        header = quantity_header(requirement.raw_text, title)
        if header is not None:
            blocks.append(_SpecBlock(requirement, header[0], header[1], []))
        line = spec_row_line(requirement.raw_text, labels)
        if line is not None:
            blocks[-1].rows.append((requirement, line))
    # Rows printed before the first quantity line belong to the first item.
    if len(blocks) > 1 and blocks[0].rows:
        blocks[1].rows[:0] = blocks[0].rows
    return [b for b in blocks[1:] if b.rows] if len(blocks) > 1 else blocks


def _block_item(
    entry: ItemRequirements,
    block: _SpecBlock,
    options: RequirementOptions,
    ontologies: Sequence[Ontology],
) -> ItemRequirements:
    """The item with one block's specification rows as requirements."""
    item = entry.item
    if block.quantity is not None:
        item = item.model_copy(update={"quantity": block.quantity, "unit": block.unit})
    added: list[Requirement] = []
    for index, (row, line) in enumerate(block.rows):
        part = item.model_copy(
            update={
                "description_lines": [line],
                "document": row.document,
                "page_number": row.page_number,
                "item_no": f"{item.item_no}.spec{index}",
            }
        )
        added.extend(
            r.model_copy(update={"item_no": item.item_no})
            for r in item_entry(part, options, ontologies).requirements
        )
    return ItemRequirements(item, entry.category, [*entry.requirements, *added], entry.product_type)


def _with_code_rows(
    found: TenderRequirements, options: RequirementOptions, ontologies: Sequence[Ontology]
) -> TenderRequirements:
    """Each item listed by a code takes the one table row keyed by that code (`code_rows`).

    The row leaves the general list; an item matching no row, or rows for several of its
    codes, is left as it was.
    """
    min_length = options.item_code_min_length
    codes = [
        codes_in([e.item.title, *(r.raw_text for r in e.requirements)], min_length)
        for e in found.items
    ]
    every = set().union(*codes) if codes else set()
    texts = [r.raw_text for r in found.general]
    taken: set[int] = set()
    items: list[ItemRequirements] = []
    for entry, own in zip(found.items, codes, strict=True):
        hits = [
            hit
            for code in sorted(own)
            if (hit := find_code_row(code, texts, every - {code}, options.item_code_row_min_cells))
        ]
        if len(hits) != 1:
            items.append(entry)
            continue
        index, row = hits[0]
        source = found.general[index]
        block = _SpecBlock(None, None, None, [(source, line) for line in row.lines])
        items.append(_block_item(entry, block, options, ontologies))
        taken.add(index)
    general = [r for i, r in enumerate(found.general) if i not in taken]
    return TenderRequirements(items, general, found.source)


def _with_spec_rows(
    found: TenderRequirements, options: RequirementOptions, ontologies: Sequence[Ontology]
) -> TenderRequirements:
    """A single nearly empty item takes the tender's specification rows from the general list.

    When the bid prints the item category's title with a quantity before each of several spec
    tables (8188046: DN100 x 70 and DN150 x 25), each table is its own item (D5).
    """
    if len(found.items) != 1:
        return found
    entry = found.items[0]
    if len(leaves(entry.requirements)) > options.quality.nearly_empty_item:
        return found
    blocks = _spec_blocks(found.general, entry.item.title, options.spec_row_labels)
    if not any(b.rows for b in blocks):
        return found
    entries = (
        [entry]
        if len(blocks) == 1
        else [_renumbered(entry, str(n)) for n in range(1, len(blocks) + 1)]
    )
    items = [_block_item(e, b, options, ontologies) for e, b in zip(entries, blocks, strict=True)]
    taken = {id(r) for b in blocks for r, _ in b.rows}
    if len(blocks) > 1:
        taken |= {id(b.header) for b in blocks if b.header is not None}
    general = [r for r in found.general if id(r) not in taken]
    return TenderRequirements(items, general, found.source)


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
        entries = [e for found in chosen.values() for e in found]
        attached, held = _attach_segments(entries, files, options, ontologies)
        items = _unique_numbers(attached)
        general = _general(files, set(), set(chosen), options, held)
        sized = [with_title_size(_once(e), options, ontologies) for e in items]
        found = TenderRequirements([_with_title(e) for e in sized], general, source)
        return _with_spec_rows(_with_code_rows(found, options, ontologies), options, ontologies)
    category = _category_entry(files, options, ontologies)
    if category is None:
        return TenderRequirements([], _general(files, set(), set(), options), ItemSource.NONE)
    entry, used = category
    general = _general(files, used, set(), options)
    parts = split_category(entry, options, ontologies)
    sized = [_with_title(with_title_size(_once(e), options, ontologies)) for e in parts]
    found = TenderRequirements(sized, general, ItemSource.ITEM_CATEGORY)
    return _with_spec_rows(_with_code_rows(found, options, ontologies), options, ontologies)


def _sized(requirement: Requirement, ontology: Ontology | None) -> bool:
    """True if a requirement states a nominal pipe size."""
    if ontology is None or requirement.attribute is None:
        return False
    attribute = ontology.attribute(requirement.attribute)
    return attribute is not None and attribute.nominal_pipe_size


def split_category(
    entry: ItemRequirements, options: RequirementOptions, ontologies: Sequence[Ontology]
) -> list[ItemRequirements]:
    """One item per product the Item Category row lists ("Globe Valve 40 NB, Globe Valve 50 NB").

    Each part is its own item (D5), read from its own title. The specification found for the
    row is shared by all of them, except a size: a part whose title states one keeps only that,
    so the 50 NB line never lands on the 40 NB item. A title of one product is left whole.

    Args:
        entry: The item made from the whole Item Category row, with the specification found.
        options: Splitting thresholds, classifier and the merged-title rule.
        ontologies: Loaded ontologies.

    Returns:
        The items, in the row's order.
    """
    title = entry.item.title
    if not title_merges_products(title, options.quality.merged_title_lead_words):
        return [entry]
    parts = [p.strip() for p in title.split(TITLE_PART_SEPARATOR) if p.strip()]
    out: list[ItemRequirements] = []
    for number, part in enumerate(parts, start=1):
        item = entry.item.model_copy(update={"title": part})
        base = item_entry(item, options, ontologies)
        ontology = ontology_for(part, ontologies)
        own_size = title_size(part) is not None
        shared = [r for r in entry.requirements if not (own_size and _sized(r, ontology))]
        whole = ItemRequirements(
            item, base.category, [*base.requirements, *shared], base.product_type
        )
        out.append(_renumbered(whole, str(number)))
    return out
