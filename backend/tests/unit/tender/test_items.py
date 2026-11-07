"""Which file a tender's items come from. Files and rows are synthetic, shaped like GeM tenders."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.tender.items import (
    ITEM_LABEL,
    ItemSource,
    TenderFile,
    item_category,
    parse_tender_files,
)
from app.tender.pipeline import RequirementOptions
from app.tender.splitter import SplitOptions, implicit_label, leaves

BACKEND = Path(__file__).resolve().parents[3]
SCHEDULE = ["Item No", "Item Title", "Description"]


@pytest.fixture(scope="module")
def options() -> RequirementOptions:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://u:p@localhost/db",  # type: ignore[arg-type]
        SECTION_CATEGORIES_FILE=BACKEND / "config" / "section_categories.yaml",
        REQUIREMENT_RULES_FILE=BACKEND / "config" / "requirement_rules.yaml",
    )
    return RequirementOptions.from_settings(settings)


@pytest.fixture(scope="module")
def ontology() -> Ontology:
    [valves] = load_ontologies(BACKEND / "ontology")
    return valves


def _row(header: list[str], cells: list[str], page: int = 1) -> ChunkDraft:
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=" | ".join(cells),
        token_count=1,
        meta={"header": header, "cells": cells},
    )


def _item(number: str, title: str, description: str = "SIZE : 100 MM") -> ChunkDraft:
    return _row(SCHEDULE, [number, title, description])


def _prose(text: str, page: int = 2) -> ChunkDraft:
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.PROSE,
        raw_text=text,
        token_count=1,
    )


CATEGORY_ROW = _row(["Column 1", "Column 2"], ["Item Category", "GATE VALVE,100MM"])


def test_item_list_comes_first(options: RequirementOptions) -> None:
    files = [
        TenderFile("boq_items.csv", "boq_items", [_item("1", "GATE VALVE", "SIZE : 100 MM")]),
        TenderFile("boq_spec.pdf", "boq_spec", [_item("7", "Name of Plant", "Some plant")]),
    ]
    found = parse_tender_files(files, options)
    assert found.source is ItemSource.ITEM_LIST
    assert [e.item.title for e in found.items] == [
        "GATE VALVE"
    ]  # no duplicates, no "Name of Plant"
    assert "Some plant" in " ".join(r.value_text for r in found.general)  # not lost


def test_a_richer_spec_schedule_replaces_a_bare_list(options: RequirementOptions) -> None:
    files = [
        TenderFile("boq_items.csv", "boq_items", [_item("1", "GATE VALVE", "SIZE : 100 MM")]),
        TenderFile(
            "boq_spec.pdf",
            "boq_spec",
            [_row(SCHEDULE, ["1", "GATE VALVE", "SIZE : 100 MM, BODY : WCB, DISC : CF8M"])],
        ),
    ]
    found = parse_tender_files(files, options)
    assert found.source is ItemSource.SPEC_SCHEDULE
    assert found.items[0].item.document == "boq_spec.pdf"


def test_the_atc_never_gives_items(options: RequirementOptions) -> None:
    files = [
        TenderFile("buyer_spec.pdf", "buyer_spec", [_item("1", "GATE VALVE")]),
        TenderFile("atc.pdf", "atc", [_item("1", "Payment term", "Within 20 days")]),
    ]
    found = parse_tender_files(files, options)
    assert [e.item.document for e in found.items] == ["buyer_spec.pdf"]
    assert any("Within 20 days" in r.value_text for r in found.general)


def test_same_number_in_two_spec_files_stays_apart(options: RequirementOptions) -> None:
    files = [
        TenderFile("buyer_spec.pdf", "buyer_spec", [_item("1", "VALVE A", "SIZE : 250 MM")]),
        TenderFile("buyer_spec_2.pdf", "buyer_spec", [_item("1", "VALVE B", "SIZE : 300 MM")]),
    ]
    found = parse_tender_files(files, options)
    assert [e.item.item_no for e in found.items] == ["buyer_spec.1", "buyer_spec_2.1"]
    assert [r.id for r in found.items[0].requirements] == ["buyer_spec.1.1"]
    assert found.items[1].requirements[0].item_no == "buyer_spec_2.1"


def test_item_category_is_the_last_source(options: RequirementOptions, ontology: Ontology) -> None:
    files = [
        TenderFile(
            "bid.pdf", "bid_document", [CATEGORY_ROW, _prose("Warranty period is 36 months.")]
        ),
        TenderFile(
            "buyer_spec.pdf", "buyer_spec", [_prose("SIZE : 100 MM\nBODY MOC : ASTM A216 WCB", 3)]
        ),
    ]
    found = parse_tender_files(files, options, [ontology])
    assert found.source is ItemSource.ITEM_CATEGORY
    [entry] = found.items
    assert (entry.item.item_no, entry.item.title) == ("1", "GATE VALVE,100MM")
    assert entry.product_type == "gate_valve"
    size, body = entry.requirements
    assert (size.value_text, body.value_text) == ("100 MM", "ASTM A216 WCB")
    assert (size.document, size.page_number, size.item_no) == ("buyer_spec.pdf", 3, "1")
    assert len({r.id for r in entry.requirements}) == 2
    assert "Warranty period is 36 months." in [r.value_text for r in found.general]
    assert not any(r.document == "buyer_spec.pdf" for r in found.general)  # used as item text


def test_several_products_in_the_category_are_not_guessed(
    options: RequirementOptions, ontology: Ontology
) -> None:
    row = _row(["Column 1", "Column 2"], ["Item Category", "GATE VALVE , GLOBE VALVE"])
    found = parse_tender_files([TenderFile("bid.pdf", "bid_document", [row])], options, [ontology])
    assert found.items[0].product_type is None


def test_item_without_a_description_keeps_its_title(options: RequirementOptions) -> None:
    files = [TenderFile("bid.pdf", "bid_document", [CATEGORY_ROW])]
    [entry] = parse_tender_files(files, options).items
    [title] = leaves(entry.requirements)
    assert (title.label, title.value_text) == (ITEM_LABEL, "GATE VALVE,100MM")


def test_size_stated_in_the_spec_is_not_repeated_from_the_title(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [
        TenderFile("bid.pdf", "bid_document", [CATEGORY_ROW]),
        TenderFile("spec.pdf", "buyer_spec", [_prose("SIZE : 100 MM")]),
    ]
    [entry] = parse_tender_files(files, options, [ontology]).items
    assert [r.value_text for r in leaves(entry.requirements)] == ["100 MM"]


def test_title_size_is_used_when_the_spec_states_none(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [TenderFile("bid.pdf", "bid_document", [CATEGORY_ROW])]
    [entry] = parse_tender_files(files, options, [ontology]).items
    [size] = leaves(entry.requirements)
    assert (size.attribute, size.value_num) == ("nominal_size", 100.0)


def test_no_item_source_still_reports_everything(options: RequirementOptions) -> None:
    files = [TenderFile("bid.pdf", "bid_document", [_prose("Warranty period is 36 months.")])]
    found = parse_tender_files(files, options)
    assert (found.source, found.items) == (ItemSource.NONE, [])
    assert len(found.general) == 1


def test_item_category_lookup() -> None:
    assert item_category([CATEGORY_ROW], ["item category"]) == ("GATE VALVE,100MM", 1)
    assert item_category([_prose("Item Category: X")], ["item category"]) is None


# ---------------------------------------------------------------- real layouts, synthetic text


def test_plural_header_and_long_spec_line(options: RequirementOptions, ontology: Ontology) -> None:
    # Layout of a buyer spec seen on a real tender: one comma list, inline keys, no title column.
    row = _row(
        ["Sr. No.", "Detailed Specifications", "Item code"],
        ["1", '"GLOBE VALVE WITH EXTENDED BONNET, SIZE:2"", PN50, BODY A351CF8"', "X1"],
    )
    found = parse_tender_files([TenderFile("s.pdf", "buyer_spec", [row])], options, [ontology])
    [entry] = found.items
    assert entry.item.title == "GLOBE VALVE WITH EXTENDED BONNET"  # the spec is not swallowed
    assert entry.product_type == "globe_valve"
    atomic = leaves(entry.requirements)
    by_label = {r.label: r for r in atomic}
    assert by_label["SIZE"].value_text == '2"'
    assert by_label["BODY"].attribute == "body_material"
    assert by_label["BODY"].value_text == "A351CF8"
    assert by_label["BODY"].raw_text == "BODY A351CF8"


SPLIT = SplitOptions(label_max_words=6, enum_max_words=2)


@pytest.mark.parametrize(
    ("part", "expected"),
    [
        ("BODY A351CF8", ("BODY", "A351CF8")),
        ("BODY MATERIAL ASTM A216", ("BODY MATERIAL", "ASTM A216")),
        ("TORQUE SEATED", None),  # no code
        ("BW ENDS", None),
        ("A351CF8", None),
    ],
)
def test_implicit_label(part: str, expected: tuple[str, str] | None, ontology: Ontology) -> None:
    assert implicit_label(part, ontology, SPLIT) == expected


def test_implicit_label_needs_an_ontology() -> None:
    assert implicit_label("BODY A351CF8", None, SPLIT) is None


def test_a_category_listing_several_sizes_is_one_item_each(
    options: RequirementOptions, ontology: Ontology
) -> None:
    # 8175587: "Globe Valve 40 NB with Companion Flanges and Fasteners , Globe Valve 50 NB ..."
    title = "Globe Valve 40 NB with Companion Flanges , Globe Valve 50 NB with Companion Flanges"
    row = _row(["Column 1", "Column 2"], ["Item Category", title])
    spec = _prose("BODY MOC : ASTM A216 WCB\nSIZE : 40 NB", 3)
    files = [
        TenderFile("bid.pdf", "bid_document", [row]),
        TenderFile("buyer_spec.pdf", "buyer_spec", [spec]),
    ]
    found = parse_tender_files(files, options, [ontology])
    first, second = found.items
    assert [e.item.item_no for e in found.items] == ["1", "2"]
    assert second.item.title == "Globe Valve 50 NB with Companion Flanges"
    assert first.product_type == second.product_type == "globe_valve"
    for entry, size in ((first, "40"), (second, "50")):
        values = [r.value_text for r in entry.requirements]
        assert "ASTM A216 WCB" in values  # the shared specification
        sizes = [r for r in entry.requirements if r.attribute == "nominal_size"]
        assert [s.value_num for s in sizes] == [float(size)]  # only its own title's size
        assert all(r.item_no == entry.item.item_no for r in entry.requirements)
    ids = [r.id for e in found.items for r in e.requirements]
    assert len(ids) == len(set(ids))


DATASHEET = ["Sl No", "Description"]


def _numbered(number: str, lines: list[str], page: int = 1) -> ChunkDraft:
    chunk = _row(DATASHEET, [number, " ".join(lines)], page)
    return chunk.model_copy(update={"meta": {**chunk.meta, "cell_lines": [[number], lines]}})


def test_each_items_datasheet_is_folded_into_it(
    options: RequirementOptions, ontology: Ontology
) -> None:
    # 8063368: items 1-2, then one numbered datasheet per item, numbering starting again.
    rows = [
        _numbered("1", ["Supply of gate valve of", "size 50 mm for steam line"]),
        _numbered("2", ["Supply of gate valve of", "size 80 mm for water line"]),
        _numbered("1", ["Size", "", "50 mm"], 2),
        _numbered("2", ["Body material", "", "ASTM A216 WCB"], 2),
        _numbered("1", ["Size", "", "80 mm"], 3),
        _numbered("2", ["Body material", "", "ASTM A351 CF8M"], 3),
    ]
    found = parse_tender_files([TenderFile("spec.pdf", "buyer_spec", rows)], options, [ontology])
    first, second = found.items
    assert first.item.title == "Supply of gate valve of size 50 mm for steam line"  # wrapped
    assert "ASTM A216 WCB" in [r.value_text for r in leaves(first.requirements)]
    assert "ASTM A351 CF8M" in [r.value_text for r in leaves(second.requirements)]
    assert "ASTM A216 WCB" not in [r.value_text for r in leaves(second.requirements)]


def test_a_line_starting_in_capitals_is_not_part_of_the_title(
    options: RequirementOptions, ontology: Ontology
) -> None:
    # 7956201: the second line is the next statement, not a wrap of the title.
    rows = [_numbered("1", ["Globe valve as per BS-1873", "Size: 6 inch # 300 Class"])]
    found = parse_tender_files([TenderFile("spec.pdf", "buyer_spec", rows)], options, [ontology])
    assert found.items[0].item.title == "Globe valve as per BS-1873"


def test_numbering_runs_that_are_not_datasheets_stay_items(
    options: RequirementOptions, ontology: Ontology
) -> None:
    rows = [
        _numbered("1", ["Gate valve 50 mm"]),
        _numbered("2", ["Gate valve 80 mm"]),
        _numbered("1", ["Globe valve 25 mm flanged, class 150"], 2),  # a product, not a label
    ]
    found = parse_tender_files([TenderFile("spec.pdf", "buyer_spec", rows)], options, [ontology])
    assert len(found.items) == 3


def test_a_control_valve_is_no_globe_valve(options: RequirementOptions, ontology: Ontology) -> None:
    title = "Supply of Pneumatic (ATO) globe type two way control valve of size 1'' for steam"
    row = _row(["Column 1", "Column 2"], ["Item Category", title])
    found = parse_tender_files([TenderFile("bid.pdf", "bid_document", [row])], options, [ontology])
    assert found.items[0].product_type is None
