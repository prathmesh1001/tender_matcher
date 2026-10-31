"""Line items -> atomic requirements. Rows and lines are synthetic, shaped like GeM specs."""

from pathlib import Path

import pytest

from app.ingestion.table_extractor import with_paragraph_breaks
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, Operator
from app.schemas.requirements import LineItem, ValueKind
from app.tender.line_items import ItemColumnOptions, find_columns, header_key, line_items
from app.tender.normalizer import parse_value, resolve_attribute
from app.tender.pipeline import ontology_for
from app.tender.splitter import SplitOptions, group_lines, item_requirements, leaves, split_parts

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
COLUMNS = ItemColumnOptions(
    item_no=("item no", "sl no", "s no"),
    title=("item title",),
    description=("description", "specification"),
    quantity_prefixes=("quant", "qty"),
    unit=("unit",),
)
SPLIT = SplitOptions(label_max_words=6, enum_max_words=2)
LINES = [
    "GATE VALVE,100MM",
    "SIZE : 100 MM",
    "PRESSURE RATING : CLASS #300",
    "MAX FLOW : 90 M3/HR",
    "VALVE BODY MOC : ASTM A216 Gr. WCB",
    "STEM MOC : A479 Type SS 410/17-4PH SS,SINGLE PIECE",
    "OPERATION : HANDWHEEL",
    "",
    "THE VALVE SHALL BE BUBBLE TIGHT, FIRE SAFE, LOW",
    "EMISSION",
]


@pytest.fixture(scope="module")
def ontology() -> Ontology:
    [valves] = load_ontologies(ONTOLOGY_DIR)
    return valves


def _item(lines: list[str] = LINES) -> LineItem:
    return LineItem(
        item_no="1",
        title="GATE VALVE,100MM",
        description_lines=lines,
        document="synthetic.pdf",
        page_number=1,
    )


# ---------------------------------------------------------------- line items


def test_header_key_folds_punctuation() -> None:
    assert header_key("S. no.") == "s no"
    assert header_key("Material Description") == "material description"


def test_find_columns() -> None:
    header = ["Item No", "Material Code", "Item Title", "Material Description", "Quantiry", "Unit"]
    columns = find_columns(header, COLUMNS)
    assert columns is not None
    assert (columns.item_no, columns.title, columns.description) == (0, 2, (3,))
    assert (columns.quantity, columns.unit) == (4, 5)  # "Quantiry" (sic) by prefix


def test_table_without_item_column_is_not_a_schedule() -> None:
    assert find_columns(["Parameter", "Description"], COLUMNS) is None


def _row(cells: list[str], cell_lines: list[list[str]] | None = None) -> ChunkDraft:
    meta: dict[str, object] = {"header": ["Item No", "Item Title", "Description"], "cells": cells}
    if cell_lines:
        meta["cell_lines"] = cell_lines
    return ChunkDraft(
        page_number=2,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=" ".join(cells),
        token_count=1,
        meta=meta,
    )


def test_line_items_use_printed_lines() -> None:
    chunks = [
        _row(
            ["1", "GATE VALVE", "SIZE : 100 MM CLASS : 300"],
            [["1"], ["GATE VALVE"], ["SIZE : 100 MM", "CLASS : 300"]],
        ),
        _row(["Note", "", "All items to be tested"]),  # not an item number
    ]
    [item] = line_items(chunks, "synthetic.pdf", COLUMNS)
    assert (item.item_no, item.title, item.page_number) == ("1", "GATE VALVE", 2)
    assert item.description_lines == ["SIZE : 100 MM", "CLASS : 300"]


def test_paragraph_breaks_come_from_line_gaps() -> None:
    lines = [(10.0, "A : 1"), (18.6, "B : 2"), (27.2, "C : 3"), (44.4, "THE VALVE SHALL")]
    assert with_paragraph_breaks(lines, 1.5) == ["A : 1", "B : 2", "C : 3", "", "THE VALVE SHALL"]


# ---------------------------------------------------------------- splitting


def test_group_lines_by_label_continuation_and_paragraph() -> None:
    groups = group_lines(LINES, "GATE VALVE,100MM", 6)
    assert [g.label for g in groups] == [
        "SIZE", "PRESSURE RATING", "MAX FLOW", "VALVE BODY MOC", "STEM MOC", "OPERATION", None,
    ]  # fmt: skip
    assert groups[-1].value == "THE VALVE SHALL BE BUBBLE TIGHT, FIRE SAFE, LOW EMISSION"


def test_long_key_is_not_a_label() -> None:
    [group] = group_lines(["NOTE THAT ALL VALVES SUPPLIED UNDER THIS ORDER : TESTED"], "X", 6)
    assert group.label is None


def test_split_parts_keeps_thousands() -> None:
    assert split_parts("A, B,C") == ["A", "B", "C"]
    assert split_parts("FLOW 1,000 M3/HR, WATER") == ["FLOW 1,000 M3/HR", "WATER"]


def test_compound_values_become_parent_and_children() -> None:
    reqs = item_requirements(_item(), None, SPLIT)
    stem = [r for r in reqs if r.label == "STEM MOC"]
    assert [(r.id, r.parent_id, r.value_text) for r in stem] == [
        ("1.5", None, "A479 Type SS 410/17-4PH SS,SINGLE PIECE"),
        ("1.5.1", "1.5", "A479 Type SS 410/17-4PH SS"),
        ("1.5.2", "1.5", "SINGLE PIECE"),
    ]
    assert stem[1].alternatives == ["A479 Type SS 410", "17-4PH SS"]
    assert stem[0] not in leaves(reqs)


def test_statement_parts_and_quotes_are_verbatim() -> None:
    reqs = item_requirements(_item(), None, SPLIT)
    parts = [r for r in leaves(reqs) if r.parent_id == "1.7"]
    assert [r.value_text for r in parts] == ["BUBBLE TIGHT", "FIRE SAFE", "LOW EMISSION"]
    assert parts[0].raw_text == "THE VALVE SHALL BE BUBBLE TIGHT"
    size = next(r for r in reqs if r.label == "SIZE")
    assert size.raw_text == "SIZE : 100 MM"


# ---------------------------------------------------------------- values


@pytest.mark.parametrize(
    ("label", "value", "kind", "operator", "number", "unit"),
    [
        ("SIZE", "250 MM", ValueKind.NUMERIC, Operator.EQ, 250.0, "mm"),
        ("PRESSURE RATING", "CLASS #150", ValueKind.NUMERIC, Operator.EQ, 150.0, "class"),
        ("MAX FLOW", "650 M3/HR", ValueKind.NUMERIC, Operator.GTE, 650.0, "m3/hr"),
        ("RATING", "PN 16 OR HIGHER", ValueKind.NUMERIC, Operator.GTE, 16.0, "pn"),
        ("SIZE", "UP TO 600 MM", ValueKind.NUMERIC, Operator.LTE, 600.0, "mm"),
        ("TEMPERATURE", "-29 to 120 degC", ValueKind.NUMERIC, Operator.RANGE, -29.0, "degc"),
        ("VALVE DESIGN", "API 609 5TH EDITION", ValueKind.CODE, Operator.IN, None, None),
        ("END CONNECTION", "WAFER", ValueKind.CODE, Operator.IN, None, None),
        ("OPERATION", "HAND GEAR OPERATED", ValueKind.TEXT, Operator.TEXT, None, None),
    ],
)
def test_parse_value(
    label: str,
    value: str,
    kind: ValueKind,
    operator: Operator,
    number: float | None,
    unit: str | None,
) -> None:
    parsed = parse_value(label, value, None, enum_max_words=2)
    assert (parsed.kind, parsed.operator, parsed.number, parsed.unit) == (
        kind, operator, number, unit,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("value", "options", "any_option"),
    [
        ("ASTM A217 Gr. CA15/CF8M", ("ASTM A217 Gr. CA15", "CF8M"), False),  # "/" alone
        ("NITRILE OR SS", ("NITRILE", "SS"), True),
        ("CA15/CF8M or A105", ("CA15", "CF8M", "A105"), True),
        ("ASTM A216 WCB or equivalent", (), False),  # D4: a qualifier, not an option
        ("ASTM A216 WCB OR BETTER", (), False),
    ],
)
def test_written_or_means_any_option(
    value: str, options: tuple[str, ...], any_option: bool
) -> None:
    parsed = parse_value("MOC", value, None, enum_max_words=3)
    assert (parsed.alternatives, parsed.any_alternative) == (options, any_option)


def test_or_higher_stays_a_direction() -> None:
    parsed = parse_value("RATING", "PN 16 OR HIGHER", None, enum_max_words=3)
    assert (parsed.operator, parsed.any_alternative) == (Operator.GTE, False)


def test_units_resolve_only_through_the_ontology(ontology: Ontology) -> None:
    assert parse_value("MAX FLOW", "650 M3/HR", None, 2).canonical_unit is None
    assert parse_value("MAX FLOW", "650 M3/HR", ontology, 2).canonical_unit == "m**3/h"
    assert parse_value("SIZE", "250 MM", ontology, 2).canonical_unit == "mm"  # not megamolar


@pytest.mark.parametrize(
    ("label", "value", "attribute"),
    [
        ("VALVE BODY MOC", "ASTM A216 Gr. WCB", "body_material"),
        ("DISC MOC", "CF8M", "disc_material"),
        ("BODY SEAT", "STELLITE 21", "seat_material"),  # the last word names the thing
        ("PRESSURE RATING", "CLASS #150", "pressure_class"),  # a designation, not bar
        ("SIZE", "250 MM", "nominal_size"),
        ("MAX FLOW", "650 M3/HR", "flow_rate"),
        ("TORQUE AT SHAFT", "820 Nm", None),  # a torque is not the shaft material
        ("FLUID", "WATER", None),
    ],
)
def test_resolve_attribute(
    ontology: Ontology, label: str, value: str, attribute: str | None
) -> None:
    parsed = parse_value(label, value, ontology, 2)
    assert resolve_attribute(label, parsed, ontology) == attribute


def test_no_ontology_means_no_attribute() -> None:
    parsed = parse_value("SIZE", "250 MM", None, 2)
    assert resolve_attribute("SIZE", parsed, None) is None


def test_ontology_chosen_by_item_title(ontology: Ontology) -> None:
    assert ontology_for("TRIPLE OFFSET BUTTERFLY VALVE,250MM", [ontology]) is ontology
    assert ontology_for("THERMOCOUPLE K TYPE", [ontology]) is None
