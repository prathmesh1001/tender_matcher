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
from app.tender.normalizer import (
    parse_value,
    resolve_attribute,
    size_form,
    temperature_form,
    title_size,
)
from app.tender.pipeline import ontology_for
from app.tender.splitter import (
    SplitOptions,
    dash_key,
    group_lines,
    item_requirements,
    leaves,
    merged_line_key,
    split_parts,
)

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
        ("ASTM A217 Gr. CA15/CF8M", ("ASTM A217 Gr. CA15", "CF8M"), True),  # "/" alone
        ("B7/2H", ("B7", "2H"), True),
        ("NITRILE OR SS", ("NITRILE", "SS"), True),
        ("CA15/CF8M or A105", ("CA15", "CF8M", "A105"), True),
        ("ASTM A216 WCB or equivalent", (), False),  # D4: a qualifier, not an option
        ("ASTM A216 WCB OR BETTER", (), False),
    ],
)
def test_or_and_slash_mean_any_option(
    value: str, options: tuple[str, ...], any_option: bool
) -> None:
    parsed = parse_value("MOC", value, None, enum_max_words=3)
    assert (parsed.alternatives, parsed.any_alternative) == (options, any_option)


def test_or_higher_stays_a_direction() -> None:
    parsed = parse_value("RATING", "PN 16 OR HIGHER", None, enum_max_words=3)
    assert (parsed.operator, parsed.any_alternative) == (Operator.GTE, False)


@pytest.mark.parametrize(
    ("label", "value", "attribute"),
    [
        # The whole label decides: a synonym followed by another word names something else.
        ("Design Temp.", "-40 Deg. C to +40 Deg. C", "max_temperature"),  # not design_standard
        ("Body / Bonnet Construction", "Bolted Bonnet", "bonnet_type"),  # never body_material
        ("STEM LENGTH", "ROUND", None),
        ("SEAT LEAKAGE", "CLASS VI", None),
        # Generic words and codes may follow a synonym.
        ("Design Standard", "BS-1873", "design_standard"),
        ("Valve Design", "API 609", "design_standard"),
        ("Body Materials WCB/ WCC", "A 216 Gr. WCB/ WCC", "body_material"),
        ("Disc Materials C5", "A 217 Gr. C5", "disc_material"),
        ("Shaft No.", "WCB", "stem_material"),
        ("Ends Parameter", "Wafer/ Lugged", "end_connection"),
        ("Body & Bonnet MOC", "LCB", "body_material"),
        ("Stem/Hinge pin", "SS316", "stem_material"),
        ("Body Materials LCB/ LCC", "A 352 Gr. LCB", "body_material"),
        ("Body & Bonnet", "LCB", "body_material"),
        ("BODY SPEC", "WCB", "body_material"),
        ("Stem Sl. No.", "WCB", "stem_material"),
        # A part of the component is not the component.
        ("Disc Nut Material", "SS 410", None),
        ("Seat/ Liner Materials", "EPDM", "seat_material"),
        ("Body Liner", "EPDM", "seat_material"),
        ("Seat Retainer", "SS 410", None),
        ("Body Gasket", "Graphite", None),
        ("Stem packing Specification", "Graphite", "packing_material"),
        # 8226616: the motor's body is not the pump's (like-for-like component).
        ("Motor Body Material", "Aluminium die casting", "motor_body_material"),
        # GeM prints the unit in brackets after the number.
        ("Motor Power", "1.5 (horsepower)", "power"),
    ],
)
def test_whole_label_decides_the_attribute(
    label: str, value: str, attribute: str | None, ontology: Ontology
) -> None:
    parsed = parse_value(label, value, ontology, 2)
    assert resolve_attribute(label, parsed, ontology) == attribute


def test_design_temp_is_a_temperature_once_the_value_is_numeric(ontology: Ontology) -> None:
    parsed = parse_value("Design Temp.", "120 degC", ontology, 2)
    assert resolve_attribute("Design Temp.", parsed, ontology) == "max_temperature"


def test_a_unit_may_follow_the_attribute(ontology: Ontology) -> None:
    parsed = parse_value("Design Pressure (Bar)", "40 bar", ontology, 2)
    assert resolve_attribute("Design Pressure (Bar)", parsed, ontology) == "pressure_rating"


@pytest.mark.parametrize(
    ("label", "value", "number", "unit", "attribute"),
    [
        # GeM bid tables print the unit in the label and leave the value bare.
        ("Nominal Size (DN) (mm)", "100", 100.0, "mm", "nominal_size"),
        ("Working Pressure (bar)", "10", 10.0, "bar", "pressure_rating"),
        # A unit next to the value wins over the label's.
        ("Nominal Size (DN) (mm)", "4 inch", 4.0, "inch", "nominal_size"),
        # Two known units in brackets: the label says nothing certain.
        ("Size (mm) (inch)", "100", 100.0, None, None),
        # A bracket that is not a unit is not one.
        ("Rated Pressure (Max)", "16", 16.0, None, None),
    ],
)
def test_a_bare_value_takes_the_unit_printed_in_its_label(
    label: str,
    value: str,
    number: float,
    unit: str | None,
    attribute: str | None,
    ontology: Ontology,
) -> None:
    parsed = parse_value(label, value, ontology, 2)
    assert (parsed.number, parsed.unit) == (number, unit)
    assert resolve_attribute(label, parsed, ontology) == attribute


@pytest.mark.parametrize(
    ("label", "value", "pn"),
    [
        ("Nominal Pressure (PN) (MPa)", "1.6", 16.0),
        ("Nominal Pressure (PN) (MPa)", "2.5", 25.0),
        ("PN", "10 bar", 10.0),
    ],
)
def test_a_pressure_under_a_pn_label_is_the_pn_designation(
    label: str, value: str, pn: float, ontology: Ontology
) -> None:
    parsed = parse_value(label, value, ontology, 2)
    assert (parsed.number, parsed.unit, parsed.is_designation) == (pn, "pn", True)
    assert resolve_attribute(label, parsed, ontology) == "pressure_class"


def test_a_pn_label_keeps_a_pressure_that_is_not_a_whole_pn(ontology: Ontology) -> None:
    parsed = parse_value("Nominal Pressure (PN) (MPa)", "1.55", ontology, 2)
    assert (parsed.number, parsed.unit) == (1.55, "mpa")
    assert resolve_attribute("Nominal Pressure (PN) (MPa)", parsed, ontology) == "pressure_rating"


def test_a_pressure_without_pn_in_its_label_stays_a_pressure(ontology: Ontology) -> None:
    parsed = parse_value("Pressure Rating", "16 bar", ontology, 2)
    assert (parsed.number, parsed.unit, parsed.is_designation) == (16.0, "bar", False)


def test_governing_specification_is_the_design_standard(ontology: Ontology) -> None:
    label = "Conformity to Governing Specification (ISS)"
    parsed = parse_value(label, "IS 13095", ontology, 2)
    assert resolve_attribute(label, parsed, ontology) == "design_standard"


def test_a_temperature_value_never_fits_a_material_label(ontology: Ontology) -> None:
    parsed = parse_value("Body Temperature", "120 degC", ontology, 2)
    assert resolve_attribute("Body Temperature", parsed, ontology) == "max_temperature"


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


# ---------------------------------------------------------------- sizes and ratings are numeric


@pytest.mark.parametrize(
    ("value", "number", "unit"),
    [
        ("DN25 MM", 25.0, "mm"),
        ("DN 80", 80.0, "mm"),
        ("DN-50", 50.0, "mm"),
        ("40 NB", 40.0, "mm"),
        ("NB 40", 40.0, "mm"),
        ("6 inch", 6.0, "inch"),
        ("3/4 inch", 0.75, "inch"),
        ('3/4"', 0.75, "inch"),
        ("1-1/2 inch", 1.5, "inch"),
        ('2.5"', 2.5, "inch"),
        ("3”", 3.0, "inch"),
        ("1’’", 1.0, "inch"),  # noqa: RUF001
        ("2 inches", 2.0, "inch"),
    ],
)
def test_size_forms_are_numeric(value: str, number: float, unit: str, ontology: Ontology) -> None:
    parsed = parse_value("SIZE", value, ontology, 2)
    assert (parsed.kind, parsed.number, parsed.unit) == (ValueKind.NUMERIC, number, unit)
    assert resolve_attribute("SIZE", parsed, ontology) == "nominal_size"


@pytest.mark.parametrize(
    ("value", "number"),
    [("# 300", 300.0), ("#800", 800.0), ("150#", 150.0), ("150 lbs", 150.0), ("Class 800", 800.0),
     ("# 300 Class", 300.0), ("300 Class", 300.0), ("CLASS #150", 150.0)],
)  # fmt: skip
def test_rating_forms_are_a_class(value: str, number: float, ontology: Ontology) -> None:
    parsed = parse_value("Rating", value, ontology, 2)
    assert (parsed.kind, parsed.number, parsed.unit) == (ValueKind.NUMERIC, number, "class")
    assert resolve_attribute("Rating", parsed, ontology) == "pressure_class"


def test_bare_number_under_a_class_label_is_a_class(ontology: Ontology) -> None:
    parsed = parse_value("CLASS", "800 OR HIGHER", ontology, 2)
    assert (parsed.number, parsed.unit, parsed.operator) == (800.0, "class", Operator.GTE)


@pytest.mark.parametrize("value", ["250", "DN25 valve", "A216", "3/4 Bore", "SCH 160"])
def test_other_values_are_not_a_size(value: str) -> None:
    assert size_form(value) is None


def _sized(line: str, title: str = "GATE VALVE") -> LineItem:
    return LineItem(
        item_no="1", title=title, description_lines=[line], document="b.pdf", page_number=1
    )


def test_size_and_rating_in_one_value_become_two(ontology: Ontology) -> None:
    reqs = item_requirements(_sized("Size: 6 inch # 300 Class"), ontology, SPLIT)
    size, rating = leaves(reqs)
    assert (size.attribute, size.value_num, size.unit) == ("nominal_size", 6.0, "inch")
    assert (rating.label, rating.attribute, rating.value_num, rating.unit) == (
        "CLASS", "pressure_class", 300.0, "class",
    )  # fmt: skip
    assert size.parent_id == rating.parent_id == "1.1"


def test_fractional_size_and_rating(ontology: Ontology) -> None:
    size, rating = leaves(item_requirements(_sized("Size: 3/4 inch # 800 Class"), ontology, SPLIT))
    assert (size.value_num, rating.value_num) == (0.75, 800.0)


def test_parts_of_a_size_line_do_not_all_become_sizes(ontology: Ontology) -> None:
    line = 'SIZE:2", PN50, BW ENDS, STEM LENGTH 220 MM MIN, BODY A351CF8'
    size, rating, ends, stem, body = leaves(item_requirements(_sized(line), ontology, SPLIT))
    assert (size.label, size.attribute, size.value_num) == ("SIZE", "nominal_size", 2.0)
    assert (rating.label, rating.attribute, rating.unit) == ("CLASS", "pressure_class", "pn")
    assert (ends.label, ends.value_text) == (None, "BW ENDS")
    assert (stem.label, stem.attribute) == (None, None)  # a length is not the stem material
    assert (body.label, body.attribute, body.value_text) == ("BODY", "body_material", "A351CF8")


def test_flanged_ended_under_size_is_no_size(ontology: Ontology) -> None:
    size, rating, ends = leaves(
        item_requirements(_sized("Size: 6 inch # 300 Class, Flanged Ended"), ontology, SPLIT)
    )
    assert (size.value_num, rating.value_num) == (6.0, 300.0)
    assert ends.label is None


def test_a_bare_size_in_a_list_is_labelled(ontology: Ontology) -> None:
    line = "THE VALVE SHALL BE WAFER TYPE, DN50, CLASS 150 OR HIGHER, 150#"
    parts = leaves(item_requirements(_sized(line), ontology, SPLIT))
    by_value = {p.value_text: p for p in parts}
    assert by_value["DN50"].attribute == "nominal_size"
    assert by_value["CLASS 150 OR HIGHER"].operator is Operator.GTE
    assert by_value["150#"].attribute == "pressure_class"


@pytest.mark.parametrize(
    ("title", "size"),
    [
        ("BALL VALVE DN20 MM", "dn20 mm"),
        ("BUTTERFLY VALVE DN 80 MM", "dn 80 mm"),
        ("TRIPLE OFFSET BUTTERFLY VALVE,250MM", "250mm"),
        ("Globe Valve of size 3inches with full bore", "3inches"),
        ("Check Valve 40 NB", "40 nb"),
        ("Globe Valve Size 25mm", "25mm"),
        ('Gate valve 1-1/2" flanged', '1-1/2"'),
        ("GATE VALVE", None),
        ("GLOBE VALVE STEM LENGTH 220 MM", None),  # a length, not a size
        ("Globe Valve 40 NB , Globe Valve 50 NB", None),  # two sizes: never guessed
        ("Globe Valve A182 F316-1.5 Inch", None),  # inside a code
    ],
)
def test_title_size(title: str, size: str | None) -> None:
    assert title_size(title) == size


# ---------------------------------------------------------------- temperature ranges


@pytest.mark.parametrize(
    ("value", "low", "high"),
    [
        ("-40 Deg. C to +40 Deg. C", -40.0, 40.0),
        ("(-196 DEG C) TO 120 DEG C", -196.0, 120.0),
        ("-29 to 120 degC", -29.0, 120.0),
        ("0 degrees C - 80 degrees C", 0.0, 80.0),
        ("356\u00b0C", 356.0, None),
    ],
)
def test_temperature_is_numeric(
    value: str, low: float, high: float | None, ontology: Ontology
) -> None:
    parsed = parse_value("Design Temp.", value, ontology, 2)
    assert (parsed.kind, parsed.number, parsed.number_max) == (ValueKind.NUMERIC, low, high)
    assert parsed.canonical_unit == "degC"
    assert resolve_attribute("Design Temp.", parsed, ontology) == "max_temperature"


def test_a_range_of_temperatures_has_the_range_operator(ontology: Ontology) -> None:
    assert parse_value("T", "-40 Deg. C to +40 Deg. C", ontology, 2).operator is Operator.RANGE


def test_up_to_a_temperature_is_an_upper_limit(ontology: Ontology) -> None:
    parsed = parse_value("Temperature", "Up to 80\u00b0 C", ontology, 2)
    assert (parsed.number, parsed.operator) == (80.0, Operator.LTE)
    assert resolve_attribute("Temperature", parsed, ontology) == "max_temperature"


@pytest.mark.parametrize("value", ["Low Temperature Test", "40 mm", "Ambient", "1020 - 1070"])
def test_other_values_are_no_temperature(value: str) -> None:
    assert temperature_form(value) is None


def test_temperature_part_of_a_list_is_labelled(ontology: Ontology) -> None:
    line = "GLOBE VALVE, TEMP DESIGN (-196 DEG C) TO 120 DEG C, BODY A351CF8"
    parts = leaves(item_requirements(_sized(line), ontology, SPLIT))
    temperature = next(p for p in parts if p.label == "TEMP DESIGN")
    assert temperature.attribute == "max_temperature"
    assert (temperature.value_min, temperature.value_max) == (-196.0, 120.0)


# ---------------------------------------------------------------- keys inside a value


@pytest.mark.parametrize(
    ("line", "label", "value"),
    [
        ("MATERIAL: Body: ASTM A216 Grade WCB", "Body", "ASTM A216 Grade WCB"),
        ("TRIM: DISC/BONNET: CI", "DISC/BONNET", "CI"),
        ("MoC & other details: DESIGN SPEC: BS 5152", "DESIGN SPEC", "BS 5152"),
        # Not keys: a sentence, a digit in the key, a web address.
        (
            "Inspection: shall be carried out as per standard IS: 14220",
            "Inspection",
            "shall be carried out as per standard IS: 14220",
        ),
        ("BODY: EN 1561 NOMINAL SIZE: 50 MM", "BODY", "EN 1561 NOMINAL SIZE: 50 MM"),
        ("website: https://example.invalid", "website", "https://example.invalid"),
        # A code's colon (scanned 8169451): the value stays whole.
        (
            "impeller : BRONZE IS:318 GR-LTB2 (110), Type -enclosed",
            "impeller",
            "BRONZE IS:318 GR-LTB2 (110), Type -enclosed",
        ),
    ],
)
def test_inline_keys(line: str, label: str, value: str) -> None:
    [group] = group_lines([line], "TITLE", 6)
    assert (group.label, group.value) == (label, value)


def test_inline_key_gives_the_attribute(ontology: Ontology) -> None:
    [body] = leaves(item_requirements(_sized("MATERIAL: Body: ASTM A216 WCB"), ontology, SPLIT))
    assert (body.label, body.attribute) == ("Body", "body_material")


def test_inline_keys_in_a_list(ontology: Ontology) -> None:
    line = "MATERIAL: Body: ASTM A216 WCB, Disc: CF8M, Seat: EPDM"
    parts = leaves(item_requirements(_sized(line), ontology, SPLIT))
    assert [p.attribute for p in parts] == ["body_material", "disc_material", "seat_material"]


# ---------------------------------------------------------------- bare millimetres


def test_bare_mm_right_after_the_item_name_is_its_size(ontology: Ontology) -> None:
    parts = leaves(item_requirements(_sized("GATE VALVE, 15MM, CLASS: 800"), ontology, SPLIT))
    by_value = {p.value_text: p for p in parts}
    assert (by_value["15MM"].label, by_value["15MM"].attribute) == ("SIZE", "nominal_size")
    assert by_value["15MM"].value_num == 15.0


def test_bare_mm_anywhere_else_stays_unlabelled(ontology: Ontology) -> None:
    parts = leaves(item_requirements(_sized("WAFER TYPE, 28 mm, CLASS: 800"), ontology, SPLIT))
    assert {p.value_text: p for p in parts}["28 mm"].label is None


# ---------------------------------------------------------------- key merged into its value


@pytest.mark.parametrize(
    ("line", "label", "attribute", "value"),
    [
        ("Pump Shaft Carbon Steel", "Pump Shaft", "stem_material", "Carbon Steel"),
        ("Casing CAST IRON IS 210: FG 2", "Casing", "casing_material", "CAST IRON IS 210: FG 2"),
        ("shaft SS ASTM A276-410ANLD", "shaft", "stem_material", "SS ASTM A276-410ANLD"),
        ("impeller BRONZE IS:318 GR", "impeller", "impeller_material", "BRONZE IS:318 GR"),
    ],
)
def test_key_merged_into_a_material_is_split(
    line: str, label: str, attribute: str, value: str, ontology: Ontology
) -> None:
    [part] = item_requirements(_sized(line), ontology, SPLIT)
    assert (part.label, part.attribute, part.value_text) == (label, attribute, value)
    assert part.raw_text == line  # the quote stays as printed


def test_a_colon_inside_the_material_is_no_key_when_a_list_follows(ontology: Ontology) -> None:
    parts = leaves(item_requirements(_sized("impeller BRONZE IS:318, CLOSED"), ontology, SPLIT))
    assert (parts[0].label, parts[0].value_text) == ("impeller", "BRONZE IS:318")
    assert parts[0].raw_text == "BRONZE IS:318"


def test_a_merged_key_starts_a_new_requirement(ontology: Ontology) -> None:
    item = LineItem(
        item_no="1",
        title="PUMP",
        description_lines=["Casing : CAST IRON", "enclosed", "shaft SS ASTM A276"],
        document="b.pdf",
        page_number=1,
    )
    casing, shaft = item_requirements(item, ontology, SPLIT)
    assert casing.value_text == "CAST IRON enclosed"
    assert shaft.attribute == "stem_material"


@pytest.mark.parametrize(
    "line",
    [
        "Shaft Nut - 1 No.",
        "Stem Length 220 mm",
        "Bronze impeller is required",
        "Cast Iron",
        "Casing : CAST IRON",
        "Casing - CAST IRON",
    ],
)
def test_lines_that_are_not_a_merged_key(line: str, ontology: Ontology) -> None:
    assert merged_line_key(line, ontology, SPLIT.label_max_words) is None


def test_a_part_that_is_no_material_does_not_keep_a_merged_key(ontology: Ontology) -> None:
    parts = leaves(
        item_requirements(_sized("impeller BRONZE IS:318, Type -enclosed"), ontology, SPLIT)
    )
    by_value = {p.value_text: p for p in parts}
    assert by_value["BRONZE IS:318"].attribute == "impeller_material"
    assert by_value["Type -enclosed"].attribute is None


def test_no_ontology_no_merged_key() -> None:
    assert merged_line_key("Pump Shaft Carbon Steel", None, SPLIT.label_max_words) is None


def test_motor_body_is_not_the_pump_body(ontology: Ontology) -> None:
    parsed = parse_value("Motor Body", "Cast Iron", ontology, SPLIT.enum_max_words)
    assert resolve_attribute("Motor Body", parsed, ontology) == "motor_body_material"


# ---------------------------------------------------------------- mixed inch fractions


@pytest.mark.parametrize(
    ("value", "inches"),
    [
        ("1 1/2''", 1.5),
        ("1 1/2'' Inch", 1.5),
        ("1 1/2''Inch", 1.5),
        ('2 1/2"', 2.5),
        ("1 1/2 Inch", 1.5),
        ("1-1/2 inch", 1.5),
        ("3/4 inch", 0.75),
        ("1.5 inch", 1.5),
    ],
)
def test_mixed_inch_fractions(value: str, inches: float) -> None:
    assert size_form(value) == (inches, "inch")


def test_title_with_a_mixed_fraction_gives_the_whole_size() -> None:
    assert size_form(title_size("BALL VALVE 1 1/2'' Inch") or "") == (1.5, "inch")


def test_a_two_digit_number_before_a_fraction_is_not_part_of_it() -> None:
    # A DN + NPS table row: DN15 next to 1/2 inch, never 15.5 inch.
    assert title_size('DN 15 1/2" VALVE') != '15 1/2"'
    assert size_form('15 1/2"') is None


# ---------------------------------------------------------------- dash separators


@pytest.mark.parametrize(
    ("part", "label", "attribute", "value"),
    [
        ("Body- CI", "Body", "body_material", "CI"),
        ("Disc- SGI", "Disc", "disc_material", "SGI"),
        ("Seat- EPDM", "Seat", "seat_material", "EPDM"),
        ("DESIGN/MFG STD- EN593", "DESIGN/MFG STD", "design_standard", "EN593"),
        ("Disc - A351 CF8M", "Disc", "disc_material", "A351 CF8M"),
        ("Bearing/Bush- SS316", "Bearing/Bush", None, "SS316"),
    ],
)
def test_a_dash_separates_key_and_value_in_a_list(
    part: str, label: str, attribute: str | None, value: str, ontology: Ontology
) -> None:
    parts = leaves(item_requirements(_sized(f"IVGK, {part}"), ontology, SPLIT))
    found = parts[-1]
    assert (found.label, found.attribute, found.value_text) == (label, attribute, value)
    assert found.raw_text == part  # the quote stays as printed


def test_a_label_does_not_carry_past_a_dash_key(ontology: Ontology) -> None:
    line = "Body : ASTM A216, DISC- A351 CF8M, Spring, Stem- SS316"
    parts = leaves(item_requirements(_sized(line), ontology, SPLIT))
    by_value = {p.value_text: p for p in parts}
    assert by_value["ASTM A216"].attribute == "body_material"
    assert by_value["A351 CF8M"].attribute == "disc_material"
    assert by_value["Spring"].attribute is None
    assert by_value["SS316"].attribute == "stem_material"


def test_a_part_before_a_dash_key_keeps_the_label(ontology: Ontology) -> None:
    parts = leaves(item_requirements(_sized("Body : A216, WCB, DISC- A351"), ontology, SPLIT))
    assert {p.value_text: p.label for p in parts}["WCB"] == "Body"


def test_a_key_wrapped_over_two_lines_is_one_part(ontology: Ontology) -> None:
    item = LineItem(
        item_no="1",
        title="CHECK VALVE",
        description_lines=["Body : ASTM A216, Type of", "Seat- nitrile, Spring"],
        document="b.pdf",
        page_number=1,
    )
    by_value = {p.value_text: p for p in leaves(item_requirements(item, ontology, SPLIT))}
    assert by_value["nitrile"].attribute == "seat_material"
    assert by_value["nitrile"].label == "Type of Seat"
    assert by_value["Spring"].attribute is None


def test_one_requirement_on_its_own_with_a_dash_key(ontology: Ontology) -> None:
    [part] = item_requirements(_sized("Disc- A351 CF8M"), ontology, SPLIT)
    assert (part.attribute, part.value_text, part.raw_text) == (
        "disc_material",
        "A351 CF8M",
        "Disc- A351 CF8M",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Butt-weld",
        "V- 415410%",
        "A216- WCB",
        "DN- 50",
        "Shaft Nut - 1 No.",
        "Non - return valve",
        "Type -enclosed",
        "wrong face-to- face length",
    ],
)
def test_dashes_that_are_no_key(text: str, ontology: Ontology) -> None:
    assert dash_key(text, ontology, SPLIT, whole_part=True) is None


def test_a_wrapped_word_alone_is_no_key(ontology: Ontology) -> None:
    assert dash_key("HARD- FACED", ontology, SPLIT, whole_part=False) is None
