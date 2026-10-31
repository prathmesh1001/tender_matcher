"""Cutting spec text at item boundaries. Text is synthetic, shaped like the T1 layouts."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType
from app.tender.classifier import load_requirement_rules
from app.tender.items import TenderFile, parse_tender_files
from app.tender.pipeline import RequirementOptions
from app.tender.segments import SegmentRules, item_clause, row_line, segment_file
from app.tender.splitter import leaves

BACKEND = Path(__file__).resolve().parents[3]
RULES = SegmentRules.from_rules(
    load_requirement_rules(BACKEND / "config" / "requirement_rules.yaml")
)
SCHEDULE = ["Item Number", "Item Title", "Item Description"]
BLANK = ["", ""]


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


def _row(
    cells: list[str], header: list[str] | None = None, page: int = 1, section: str | None = None
) -> ChunkDraft:
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=" | ".join(cells),
        section=section,
        token_count=1,
        meta={"header": header if header is not None else BLANK, "cells": cells},
    )


def _prose(text: str, page: int = 1) -> ChunkDraft:
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.PROSE,
        raw_text=text,
        token_count=1,
    )


def _texts(chunks: list[ChunkDraft], numbers: set[str]) -> dict[tuple[str, ...], list[str]]:
    found = segment_file(chunks, numbers, RULES)
    return {s.item_nos: [ln.text for ln in s.lines if ln.text] for s in found.segments}


# ---------------------------------------------------------------- one item at a time


def test_item_starts_cut_prose_even_mid_paragraph() -> None:
    chunks = [
        _prose("Item 1 - CS0108100 - Globe valve, Body WCB\nTest Certificate : IBR null Item 2 - "
               "CS0108200 - Check valve, Body WC9"),
    ]  # fmt: skip
    assert _texts(chunks, {"1", "2"}) == {
        ("1",): ["Globe valve, Body WCB", "Test Certificate : IBR"],
        ("2",): ["Check valve, Body WC9"],
    }


def test_text_stays_with_its_item_until_the_next() -> None:
    chunks = [
        _prose("Item 1 - Globe valve"),
        _row(["1", "Interchangeability certificate"], ["Fact or No", "Factor Description"]),
        _prose("Item 2 - Check valve"),
        _row(["1", "Spare seal"], ["Fact or No", "Factor Description"]),
    ]
    found = _texts(chunks, {"1", "2"})
    assert found[("1",)] == ["Globe valve", "Interchangeability certificate"]
    assert found[("2",)] == ["Check valve", "Spare seal"]


def test_a_table_header_can_start_an_item() -> None:
    header = ["Item 2 - CS0108200 - Check valve", "Column 2"]
    assert _texts([_prose("Item 1 - A"), _row(["1", "Seal"], header)], {"1", "2"})[("2",)] == [
        "Seal"
    ]


def test_a_carried_header_does_not_move_text_back() -> None:
    # A wrong header carried across a page: its rows belong to the item printed last.
    old = ["Item 1 - Globe valve", "Column 2"]
    chunks = [
        _row(["1", "First seal"], old),
        _prose("Item 2 - Check valve"),
        _row(["1", "Second seal"], old),
    ]
    found = segment_file(chunks, {"1", "2"}, RULES)
    assert [s.item_nos for s in found.segments] == [("1",), ("2",)]


def test_a_repeated_list_ends_the_spec() -> None:
    chunks = [
        _prose("Item 1 - Globe valve"),
        _prose("Item 2 - Check valve"),
        _row(["Item 1 - Globe valve", "0"], ["Delivery Type", "Period"]),
        _row(["BULK SUPPLY", "365"], ["Delivery Type", "Period"]),
    ]
    found = segment_file(chunks, {"1", "2"}, RULES)
    assert [s.item_nos for s in found.segments] == [("1",), ("2",)]
    assert found.used_chunks == {0, 1}  # the delivery rows stay general


@pytest.mark.parametrize("text", ["ANNEXURE- 3 Technical Terms", "II. DELIVERY PERIOD"])
def test_an_end_marker_ends_the_spec(text: str) -> None:
    chunks = [_prose("Item 1 - Globe valve"), _prose(text), _prose("Item 2 - Check valve")]
    found = segment_file(chunks, {"1", "2"}, RULES)
    assert [s.item_nos for s in found.segments] == [("1",)]


def test_numbers_that_are_not_items_are_not_boundaries() -> None:
    text = "Item 1 - Globe valve\nsee item 99 - it is not listed"
    assert list(_texts([_prose(text)], {"1"})) == [("1",)]


def test_form_text_is_cut() -> None:
    text = "Item 1 - Test Certificate : IBR Agreed for the above. Name of Authorised Signatory :"
    assert _texts([_prose(text)], {"1"}) == {("1",): ["Test Certificate : IBR"]}


def test_text_before_the_first_item_stays_general() -> None:
    chunks = [_prose("Bidders must read this."), _prose("Intro line. Item 1 - Globe valve")]
    found = segment_file(chunks, {"1"}, RULES)
    assert found.used_chunks == set()  # the second chunk also holds text no item owns


def test_no_boundaries_no_segments() -> None:
    found = segment_file([_prose("Plain specification text")], {"1"}, RULES)
    assert (found.segments, found.used_chunks) == ([], set())


# ---------------------------------------------------------------- shared blocks


def test_shared_block_applies_to_all_its_items() -> None:
    chunks = [
        _row(["For Item Sl. No.", "1, 2 & 3"]),
        _row(["Design Standard", "BS-1873"]),
        _row(["For Item Sl. No.", "4 & 5"]),
        _row(["Design Standard", "API 600"]),
    ]
    assert _texts(chunks, {"1", "2", "3", "4", "5"}) == {
        ("1", "2", "3"): ["Design Standard : BS-1873"],
        ("4", "5"): ["Design Standard : API 600"],
    }


@pytest.mark.parametrize(
    ("item_no", "expected"),
    [("4", "Rating : # 300"), ("6", "Rating : # 300"), ("7", "Rating : #800"), ("9", None)],
)
def test_clause_for_one_item_prefix_form(item_no: str, expected: str | None) -> None:
    line = "Rating : For Item Sl. No-4, 5 & 6 # 300 & For Item Sl No- 7 #800"
    assert item_clause(line, item_no, RULES) == expected


@pytest.mark.parametrize(
    ("item_no", "expected"),
    [("4", "Design Standard : API 600"), ("7", "Design Standard : API 602")],
)
def test_clause_with_a_colon_after_the_items(item_no: str, expected: str) -> None:
    line = "Design Standard : For Item Sl. No-4, 5 & 6: API 600 & For Item Sl No- 7: API 602"
    assert item_clause(line, item_no, RULES) == expected


@pytest.mark.parametrize(
    ("item_no", "expected"),
    [("5", "Body MOC : LCB"), ("7", "Body MOC : LCB -> LF2")],
)
def test_clause_for_one_item_suffix_form(item_no: str, expected: str) -> None:
    line = "Body MOC : LCB for Item Sl. No-4, 5 & 6 /LCB -> LF2 only for Item Sl. No-7"
    assert item_clause(line, item_no, RULES) == expected


def test_line_naming_no_items_is_unchanged() -> None:
    assert item_clause("Trim : 12 -> 16", "4", RULES) == "Trim : 12 -> 16"


def test_row_line() -> None:
    assert row_line(_row(["Rating", "# 300"])) == "Rating : # 300"
    assert row_line(_row(["1", "A long description", ""], ["No", "Description", "Reply"])) == (
        "A long description"
    )


# ---------------------------------------------------------------- attached to items


def _list_row(number: str, title: str) -> ChunkDraft:
    return _row([number, title, title], SCHEDULE)


def test_block_rows_reach_each_item_with_their_own_page_and_value(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [
        TenderFile(
            "boq_items.csv",
            "boq_items",
            [_list_row("1", "GATE VALVE A"), _list_row("2", "GATE VALVE B")],
        ),
        TenderFile(
            "boq_spec.pdf",
            "boq_spec",
            [
                _row(["For Item Sl. No.", "1 & 2"], page=6),
                _row(["Testing Standard", "API 598"], page=6),
                _row(
                    ["Design Standard", "For Item Sl. No-1: API 600 & For Item Sl No- 2: API 602"],
                    page=7,
                ),
                _prose("ANNEXURE- 3 Terms", page=8),
                _prose("Warranty period is 36 months.", page=8),
            ],
        ),
    ]
    found = parse_tender_files(files, options, [ontology])
    first, second = (leaves(e.requirements) for e in found.items)
    assert [(r.label, r.value_text) for r in first] == [
        ("Testing Standard", "API 598"), ("Design Standard", "API 600"),
    ]  # fmt: skip
    assert second[1].value_text == "API 602"
    assert (first[0].document, first[0].page_number, first[0].item_no) == ("boq_spec.pdf", 6, "1")
    assert first[1].page_number == 7
    assert len({r.id for e in found.items for r in e.requirements}) == 4
    general = [r.value_text for r in found.general]
    assert "Warranty period is 36 months." in general  # after the end marker
    assert not any("API 598" in text for text in general)  # now held by the items


def test_item_text_does_not_reach_other_items(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [
        TenderFile(
            "boq_items.csv",
            "boq_items",
            [_list_row("11", "BALL VALVE DN40"), _list_row("12", "BALL VALVE DN50")],
        ),
        TenderFile(
            "boq_spec.pdf",
            "boq_spec",
            [_prose("Item 11 - End details : Screwed female NPT\nItem 12 - End details : Flanged")],
        ),
    ]
    first, second = parse_tender_files(files, options, [ontology]).items
    assert [r.value_text for r in leaves(first.requirements)] == ["Screwed female NPT"]
    assert [r.value_text for r in leaves(second.requirements)] == ["Flanged"]


def test_repeated_text_gives_one_requirement(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [
        TenderFile("boq_items.csv", "boq_items", [_list_row("1", "GATE VALVE")]),
        TenderFile(
            "boq_spec.pdf",
            "boq_spec",
            [
                _prose("Item 1 - Body : WCB, Disc : CF8M"),
                _row(["1", "Body : WCB, Disc : CF8M"], ["Fact or No", "Factor Description"]),
            ],
        ),
    ]
    [entry] = parse_tender_files(files, options, [ontology]).items
    assert sorted(r.value_text for r in leaves(entry.requirements)) == ["CF8M", "WCB"]
    assert [r for r in entry.requirements if r.parent_id is None and r.id.endswith(".1")]


def test_title_only_item_still_keeps_its_title(options: RequirementOptions) -> None:
    files = [
        TenderFile("boq_items.csv", "boq_items", [_list_row("1", "GATE VALVE")]),
        TenderFile("boq_spec.pdf", "boq_spec", [_prose("Plain specification text")]),
    ]
    [entry] = parse_tender_files(files, options).items
    assert [r.value_text for r in leaves(entry.requirements)] == ["GATE VALVE"]
