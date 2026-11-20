"""Which file a tender's items come from. Files and rows are synthetic, shaped like GeM tenders."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.ontology.loader import load_ontology_file
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
    valves = load_ontology_file(BACKEND / "ontology" / "valves_pumps.yaml")
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


BFV_TITLE = "Butterfly Valves (V2) as per IS 13095 (Q2)"


def _gem_row(name: str, value: str, page: int) -> ChunkDraft:
    header = ["Specification", "Specification Name", "Bid Requirement (Allowed Values)"]
    cells = ["GENERIC", name, value]
    text = "; ".join(f"{h}: {c}" for h, c in zip(header, cells, strict=True))
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=text,  # printed like the bid's rows: "Specification Name: ...; Bid ...: ..."
        token_count=1,
        meta={"header": header, "cells": cells},
    )


def _bfv_bid(*blocks: tuple[str, str, int]) -> list[TenderFile]:
    """A bid like 8188046: the item category, then per item its quantity line and spec table."""
    chunks = [_row(["Column 1", "Column 2"], ["Item Category", BFV_TITLE])]
    for quantity, size, page in blocks:
        chunks.append(_prose(f"Butterfly Valves (V2) As Per IS 13095 ( {quantity} pieces )", page))
        chunks.append(_gem_row("Conformity to Governing Specification (ISS)", "IS 13095", page))
        chunks.append(_gem_row("Nominal Size (DN) (mm)", size, page))
    return [TenderFile("bid.pdf", "bid_document", chunks)]


def test_each_quantity_block_of_the_category_is_its_own_item(
    options: RequirementOptions, ontology: Ontology
) -> None:
    found = parse_tender_files(_bfv_bid(("70", "100", 4), ("25", "150", 5)), options, [ontology])
    assert [(e.item.item_no, e.item.quantity) for e in found.items] == [("1", "70"), ("2", "25")]
    for entry, size, page in zip(found.items, ["100", "150"], [4, 5], strict=True):
        specs = [r for r in leaves(entry.requirements) if r.label != ITEM_LABEL]
        assert [r.value_text for r in specs] == ["IS 13095", size]  # the standard on both
        assert {r.page_number for r in specs} == {page}
        assert {r.item_no for r in entry.requirements} == {entry.item.item_no}
        assert len({r.id for r in entry.requirements}) == len(entry.requirements)
    assert not any("pieces" in r.raw_text for r in found.general)  # quantity lines used


def test_one_quantity_block_stays_one_item(options: RequirementOptions, ontology: Ontology) -> None:
    found = parse_tender_files(_bfv_bid(("70", "100", 4)), options, [ontology])
    [entry] = found.items
    assert (entry.item.item_no, entry.item.quantity, entry.item.unit) == ("1", "70", "pieces")
    specs = [r.value_text for r in leaves(entry.requirements) if r.label != ITEM_LABEL]
    assert specs == ["IS 13095", "100"]


def test_a_lost_quantity_line_still_starts_the_next_item(
    options: RequirementOptions, ontology: Ontology
) -> None:
    """8148400 p7: the quantity line sat inside a table at the page top and was lost."""
    [bid] = _bfv_bid(("20", "50", 5), ("50", "65", 6))
    chunks = list(bid.chunks)
    chunks += [
        _gem_row("Conformity to Governing Specification (ISS)", "IS 13095", 7),
        _gem_row("Nominal Size (DN) (mm)", "80", 7),
        _prose(
            "Additional Specification Parameters - Butterfly Valves (V2) As Per IS 13095 "
            "( 50 pieces )\n7 / 20",
            7,
        ),
    ]
    found = parse_tender_files([TenderFile("bid.pdf", "bid_document", chunks)], options, [ontology])
    sizes = [
        [r.value_text for r in leaves(e.requirements) if r.attribute == "nominal_size"]
        for e in found.items
    ]
    assert sizes == [["50"], ["65"], ["80"]]
    assert [e.item.quantity for e in found.items] == ["20", "50", "50"]


def test_a_row_printed_again_at_a_page_break_is_kept_once(
    options: RequirementOptions, ontology: Ontology
) -> None:
    [bid] = _bfv_bid(("50", "100", 8))
    pressure = _gem_row("Nominal Pressure (PN) (MPa)", "0.25, 0.6, 1, 2.5, 4, 1.6", 9)
    chunks = [*bid.chunks, pressure, pressure]
    found = parse_tender_files([TenderFile("bid.pdf", "bid_document", chunks)], options, [ontology])
    [entry] = found.items
    assert [r.label for r in leaves(entry.requirements)].count("Nominal Pressure (PN) (MPa)") == 1


def test_allowed_values_are_options_not_parts(
    options: RequirementOptions, ontology: Ontology
) -> None:
    """GeM "Bid Requirement (Allowed Values)": any listed value is acceptable (8148400)."""
    [bid] = _bfv_bid(("20", "50", 5))
    application = _gem_row("Application", "Tight Shut Off, Regulating, Low Leakage", 5)
    pressure = _gem_row("Nominal Pressure (PN) (MPa)", "0.25, 0.6, 1, 2.5, 4, 1.6", 5)
    long_name = _gem_row(
        "Face to Face Dimension (In this context, the final decision made by the consignee/ "
        "Buyer shall be binding)",
        "Double Flanged Short (PN 2.5) Class 125/150, Wafer(PN 4.0) Class 300",
        5,
    )
    chunks = [*bid.chunks, application, pressure, long_name]
    found = parse_tender_files([TenderFile("bid.pdf", "bid_document", chunks)], options, [ontology])
    by_label = {r.label: r for r in leaves(found.items[0].requirements)}
    app = by_label["Application"]
    assert app.alternatives == ["Tight Shut Off", "Regulating", "Low Leakage"]
    assert app.any_alternative
    pn = by_label["Nominal Pressure (PN) (MPa)"]
    assert (pn.kind.value, pn.any_alternative) == ("numeric", True)
    assert pn.alternatives == ["0.25", "0.6", "1", "2.5", "4", "1.6"]
    f2f = next(r for label, r in by_label.items() if label and label.startswith("Face to Face"))
    assert f2f.alternatives == [
        "Double Flanged Short (PN 2.5) Class 125/150",
        "Wafer(PN 4.0) Class 300",
    ]


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


def test_title_rating_is_used_when_the_spec_states_none(
    options: RequirementOptions, ontology: Ontology
) -> None:
    """8222933: "Rating 300" only in the title; without it a knife-edge valve looked full."""
    row = _row(["Column 1", "Column 2"], ["Item Category", "2 Inch Gate Valve 51430 Rating 300"])
    spec = _prose("VLV.GATE, SHEET 51430, 2.0 INCH", 3)
    files = [
        TenderFile("bid.pdf", "bid_document", [row]),
        TenderFile("s.pdf", "buyer_spec", [spec]),
    ]
    [entry] = parse_tender_files(files, options, [ontology]).items
    ratings = [r for r in leaves(entry.requirements) if r.unit == "class"]
    assert [(r.value_num, r.raw_text) for r in ratings] == [
        (300.0, "2 Inch Gate Valve 51430 Rating 300")
    ]


def test_a_rating_stated_in_the_spec_is_not_repeated_from_the_title(
    options: RequirementOptions, ontology: Ontology
) -> None:
    row = _row(["Column 1", "Column 2"], ["Item Category", "Gate Valve 2 inch Class 300"])
    spec = _prose("PRESSURE RATING : CLASS 600", 3)
    files = [
        TenderFile("bid.pdf", "bid_document", [row]),
        TenderFile("s.pdf", "buyer_spec", [spec]),
    ]
    [entry] = parse_tender_files(files, options, [ontology]).items
    assert [r.value_num for r in leaves(entry.requirements) if r.unit == "class"] == [600.0]


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
    # Its own type since GeM discovery (2026-11-18); the exact phrase wins over globe's words.
    assert found.items[0].product_type == "control_valve"
