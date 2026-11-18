"""Annexure blocks headed by item names (8157758 layout). Text is synthetic, OCR-shaped."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.ontology.loader import load_ontology_file
from app.ontology.schema import Ontology
from app.ontology.units import spell_fractions
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, RequirementType
from app.tender.classifier import load_requirement_rules
from app.tender.items import TenderFile, parse_tender_files
from app.tender.name_segments import NameRules, item_name, segment_by_name, set_line
from app.tender.pipeline import RequirementOptions
from app.tender.splitter import leaves

BACKEND = Path(__file__).resolve().parents[3]
_RULES = NameRules.from_rules(load_requirement_rules(BACKEND / "config" / "requirement_rules.yaml"))
assert _RULES is not None
RULES: NameRules = _RULES
SCHEDULE = ["Item Number", "Item Title", "Item Description"]
TITLES = {
    "2": "Hex head bolt set as per Annexure A",
    "6": "SS hex head bolt set as per Annexure",
    "11": "SS ball valve set as per Annexure A",
    "23": "GI pipe nipple set as per Annexure A",
}


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


def _prose(text: str, page: int = 1) -> ChunkDraft:
    return ChunkDraft(
        page_number=page, position=0, chunk_type=ChunkType.PROSE, raw_text=text, token_count=1
    )


def _list_row(number: str, title: str) -> ChunkDraft:
    cells = [number, title, title]
    return ChunkDraft(
        page_number=1,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=" | ".join(cells),
        token_count=1,
        meta={"header": SCHEDULE, "cells": cells},
    )


def _texts(text: str) -> dict[tuple[str, ...], list[str]]:
    found = segment_by_name([_prose(text)], TITLES, RULES)
    return {s.item_nos: [ln.text for ln in s.lines] for s in found.segments}


def test_item_name_drops_the_annexure_reference() -> None:
    # Names are kept in matching form: OCR-confused letters folded alike ("l" reads as "i").
    assert item_name("SS ball valve set as per Annexure A", RULES) == ("ss", "baii", "vaive")
    assert item_name("SS hex head bolt set as per Annexure", RULES) == ("ss", "hex", "head", "boit")


def test_only_titles_pointing_to_an_annexure_name_blocks() -> None:
    assert item_name("Butterfly valve with Worm Gear & Handwheel Model:", RULES) == ()
    titles = {"1": "Butterfly valve with Worm Gear & Handwheel Model:"}
    text = "Butterfly valve with Worm Gear & Handwheel Model: IVGK\nBody - CI"
    assert segment_by_name([_prose(text)], titles, RULES).segments == []


def test_longest_name_wins_and_stray_sl_no_is_skipped() -> None:
    text = (
        "Hex head bolt set - 1 set shall consists of the following bolts\n"
        "M8 x 1.25 x 100 - 100 nos\n"
        "SS Hex head bolt set - 1 set shall consists of the following SS bolts\n"
        "M16 x 2 x 120 - 50 nos\n"
        "11 SS ball valve set - 1 set shall consists of the following SS ball values\n"
        '1"- 10 nos\n'
    )
    assert _texts(text) == {
        ("2",): [
            "Hex head bolt set - 1 set shall consists of the following bolts",
            "Set contents : M8 x 1.25 x 100",
        ],
        ("6",): [
            "SS Hex head bolt set - 1 set shall consists of the following SS bolts",
            "Set contents : M16 x 2 x 120",
        ],
        ("11",): [
            "11 SS ball valve set - 1 set shall consists of the following SS ball values",
            'Size : 1"',
        ],
    }


def test_ocr_confused_letters_still_open_the_block() -> None:
    text = "Gl pipe nipple set - 1 set shall consists of the following GI pipe nipples\n2”- 20 nos"
    assert _texts(text) == {
        ("23",): [
            "Gl pipe nipple set - 1 set shall consists of the following GI pipe nipples",
            "Size : 2”",
        ]
    }


def test_name_inside_a_sentence_is_no_heading() -> None:
    text = "Supply of SS ball valves as per the drawing\nWarranty 12 months"
    assert _texts(text) == {}


@pytest.mark.parametrize(
    ("line", "expected", "unreadable"),
    [
        ('1"- 10 nos', 'Size : 1"', False),
        ("3\u2019\u2019- 1 no", "Size : 3\u2019\u2019", False),
        ('1-1/2" - 2 nos', 'Size : 1-1/2"', False),
        ("DN 25 - 4 nos", "Size : DN 25", False),
        ("M6 x 1 x 20 - 200 nos", "Set contents : M6 x 1 x 20", False),
        ("%”- 15 nos", "Size : %”", True),  # OCR lost 1/2" or 3/4"
        ("h”- 3 nos", "Size : h”", True),  # no digit left
    ],
)
def test_set_lines(line: str, expected: str, unreadable: bool) -> None:
    found = set_line(line, RULES)
    assert found is not None
    assert (found.text, found.unreadable) == (expected, unreadable)


def test_other_lines_are_not_set_lines() -> None:
    assert set_line("Page 3/7", RULES) is None
    assert set_line("Emery sheets of 9 x 11 size", RULES) is None


def test_annexure_reaches_items_and_lost_sizes_go_to_review(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [
        TenderFile("boq_items.csv", "boq_items", [_list_row("11", TITLES["11"])]),
        TenderFile(
            "boq_spec.pdf",
            "boq_spec",
            [
                _prose("Annexure A\nSpecification:\nNomenclature", page=1),
                _prose(
                    "11 SS ball valve set - 1 set shall consists of the following SS ball values\n"
                    "%”- 15 nos\n1”- 10 nos",
                    page=3,
                ),
            ],
        ),
    ]
    [entry] = parse_tender_files(files, options, [ontology]).items
    found = {r.raw_text: r for r in leaves(entry.requirements)}
    size = found["Size : 1”"]
    assert (size.attribute, size.value_num, size.unit, size.page_number) == (
        "nominal_size",
        1.0,
        "inch",
        3,
    )
    lost = found["Size : %”"]
    assert lost.req_type is RequirementType.VAGUE
    assert lost.flags == ["value not readable in the scan: check the document"]
    assert all(r.item_no == "11" for r in found.values())


def test_numbered_spec_keeps_number_boundaries(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [
        TenderFile("boq_items.csv", "boq_items", [_list_row("11", TITLES["11"])]),
        TenderFile(
            "boq_spec.pdf",
            "boq_spec",
            [_prose("Item 11 - Ball valve, Body CF8M\nSS ball valve set - 1 set of\n2”- 1 no")],
        ),
    ]
    [entry] = parse_tender_files(files, options, [ontology]).items
    texts = [r.raw_text for r in leaves(entry.requirements)]
    assert not any(t.startswith("Size :") for t in texts)  # name blocks only without numbers


@pytest.mark.parametrize(
    ("printed", "spelled"),
    [
        ("1½”", "1 1/2”"),
        ("¾ inch", "3/4 inch"),
        ("2⅜", "2 3/8"),
        ("3/4", "3/4"),
    ],
)
def test_fraction_characters_are_spelled_out(printed: str, spelled: str) -> None:
    assert spell_fractions(printed) == spelled


def test_one_and_a_half_inch_is_1_5_not_5_5(
    options: RequirementOptions, ontology: Ontology
) -> None:
    files = [
        TenderFile("boq_items.csv", "boq_items", [_list_row("11", TITLES["11"])]),
        TenderFile(
            "boq_spec.pdf", "boq_spec", [_prose("11 SS ball valve set - 1 set\n1½”- 2 nos")]
        ),
    ]
    [entry] = parse_tender_files(files, options, [ontology]).items
    [size] = [r for r in leaves(entry.requirements) if r.attribute == "nominal_size"]
    assert (size.value_num, size.unit) == (1.5, "inch")
