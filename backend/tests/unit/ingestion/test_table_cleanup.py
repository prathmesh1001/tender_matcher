"""Continuation-row merging (failure case V4). All rows are synthetic, shaped like real layouts."""

import pytest

from app.ingestion.table_cleanup import (
    caption_text,
    is_caption_header,
    is_continuation_row,
    is_split_value,
    merge_continuation_rows,
    merge_split_values,
    split_caption_header,
)
from app.schemas.chunking import TableRow
from app.schemas.parsing import BBox


def _row(*cells: str, y: float = 0) -> TableRow:
    return TableRow(cells=list(cells), bbox=BBox(x0=0, y0=y, x1=100, y1=y + 10))


def _cells(rows: list[TableRow]) -> list[list[str]]:
    return [r.cells for r in rows]


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize(
    ("above", "cells"),
    [
        (["Impeller", "Cast Iron / Bronze"], ["/Stainless Steel", "/Stainless Steel"]),  # "/"
        (["Seal", "Gland packed"], ["or mechanical seal", ""]),  # "or ..."
        (["Gasket", "Spiral-wound SS 304"], ["", "with graphite filler"]),  # lower-case word
        (["Power", "0.5 to 20 kW"], ["", "(1 to 27 HP)"]),  # bracketed note, empty label
        (["Insulation", "B class (to 7.5 HP)"], ["", "/ F class (above 7.5 HP)"]),
        (["Disc", "CF8, CF8M to A 351,"], ["", "CF3M"]),  # value above stops mid-phrase
        (["Stud", "ASTM A276 Type 410 /"], ["", "ASTM A194 Gr. 2H"]),
    ],
)
def test_continuation_rows(above: list[str], cells: list[str]) -> None:
    assert is_continuation_row(above, cells)


@pytest.mark.parametrize(
    ("above", "cells"),
    [
        (["Head", "Up to 80 m"], ["Flow", "Up to 50 lps"]),  # a new attribute
        (["20", "Handwheel Nut", "Malleable Iron"], ["", "Nameplate", "SS 304"]),  # new part
        (["Gasket", "", "150", "Graphite"], ["", "", "600", "Spiral-wound"]),  # class rows
        (["L", "1 - Single-piece"], ["", "2 - Two-piece"]),  # ordering-code legend
        (["Size", "50 mm"], ["", "80 mm"]),  # a second value, not a wrapped one
        (["Body", "", "Cast Iron"], ["", "Liner", "Steel"]),  # value under an empty cell
        (["Body", "Cast Iron"], ["", "", ""]),  # empty row
        (["Size", "DN 50"], ["d", "12"]),  # single-letter dimension symbol
    ],
)
def test_rows_that_stay_separate(above: list[str], cells: list[str]) -> None:
    assert not is_continuation_row(above, cells)


def test_empty_label_row_under_unlabelled_row_stays() -> None:
    assert not is_continuation_row(["", "Shell"], ["", "Seat"])


# ---------------------------------------------------------------- merging


def test_merge_is_cell_by_cell() -> None:
    rows = [
        _row("Impeller", "Cast Iron / Bronze", "Cast Iron", y=0),
        _row("/Stainless Steel", "/Stainless Steel", "", y=10),
        _row("Shaft", "Carbon Steel", "Carbon Steel", y=20),
    ]
    merged = merge_continuation_rows(rows)
    assert _cells(merged) == [
        ["Impeller /Stainless Steel", "Cast Iron / Bronze /Stainless Steel", "Cast Iron"],
        ["Shaft", "Carbon Steel", "Carbon Steel"],
    ]
    assert merged[0].bbox == BBox(x0=0, y0=0, x1=100, y1=20)


def test_several_continuation_lines_join_one_row() -> None:
    rows = [
        _row("Voltage", "300 to 440 V", y=0),
        _row("", "(three phase)", y=10),
        _row("", "or single phase", y=20),
    ]
    assert _cells(merge_continuation_rows(rows)) == [
        ["Voltage", "300 to 440 V (three phase) or single phase"]
    ]


def test_first_row_is_never_merged() -> None:
    # The header is separate, so a first body row with an empty label (series names read as
    # data, V3) has nothing above it to join.
    rows = [_row("", "Series A", "Series B"), _row("Head", "Up to 80 m", "Up to 40 m")]
    assert _cells(merge_continuation_rows(rows)) == _cells(rows)


def test_shorter_continuation_row_is_padded() -> None:
    merged = merge_continuation_rows([_row("Seal", "Gland", "Gland"), _row("/Mechanical")])
    assert _cells(merged) == [["Seal /Mechanical", "Gland", "Gland"]]


def test_stacked_flag_is_kept() -> None:
    rows = [
        TableRow(
            cells=["Disc", "CF8,"], bbox=BBox(x0=0, y0=0, x1=1, y1=1), stacked_unresolved=True
        ),
        _row("", "CF8M"),
    ]
    assert merge_continuation_rows(rows)[0].stacked_unresolved


# ---------------------------------------------------------------- split values (V5)

UNITS = frozenset({"bar", "°c", "m3/h", "kg/cm2", "mm"})


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Up to 16", "bar"),  # number, then a unit-only cell
        ("Max. 10", "kg/cm2"),
        ("-30 °C to", "+140 °C"),  # open range, then the value that closes it
        ("50 to", "600 mm"),
        ("-20 °C to", "\N{MINUS SIGN}5 °C"),  # typographic minus sign
    ],
)
def test_split_values(left: str, right: str) -> None:
    assert is_split_value(left, right, UNITS)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Up to 16", "Up to 10"),  # two values
        ("Cast iron", "bar"),  # no number before the unit
        ("Up to 16", ""),
        ("Suitable to", "Water"),  # "to" not followed by a value
        ("16", "bars of steel"),  # not only a unit
    ],
)
def test_not_split_values(left: str, right: str) -> None:
    assert not is_split_value(left, right, UNITS)


def test_split_value_goes_to_every_spanned_column() -> None:
    row = _row("Pressure", "Up to 16", "bar")
    merged = merge_split_values(row, UNITS)
    assert merged.cells == ["Pressure", "Up to 16 bar", "Up to 16 bar"]
    assert merged.spanning_columns == (1, 2)


def test_open_range_split_value() -> None:
    merged = merge_split_values(_row("Fluid temperature", "-30 °C to", "+140 °C"), UNITS)
    assert merged.cells[1:] == ["-30 °C to +140 °C", "-30 °C to +140 °C"]


def test_label_column_is_never_joined() -> None:
    # "Pressure" | "bar" | "16": a label next to a unit column is not a split value.
    row = _row("16", "bar", "Rating")
    assert merge_split_values(row, UNITS) is row


def test_complete_values_are_untouched() -> None:
    row = _row("Head", "Up to 160 m", "Up to 101 m")
    assert merge_split_values(row, UNITS) is row


# ---------------------------------------------------------------- captions (V2, V3)

MAX_LABEL_WORDS = 8
CAPTION_HEADER = [
    "Column 1",
    "PERFORMANCE CHART FOR",
    "'XA' SERIES,",
    "2 POLE,",
    "MONOBLOC PUMPS, AT RATED VOLTAGE, 50 Hz FREQUENCY, THREE PHASE SUPPLY",
]


def test_caption_sentence_is_not_a_header() -> None:
    assert is_caption_header(CAPTION_HEADER, MAX_LABEL_WORDS)
    assert caption_text(CAPTION_HEADER) == (
        "PERFORMANCE CHART FOR 'XA' SERIES, 2 POLE, MONOBLOC PUMPS, AT RATED VOLTAGE, 50 Hz "
        "FREQUENCY, THREE PHASE SUPPLY"
    )


@pytest.mark.parametrize(
    "header",
    [
        ["Model", "Power kW", "Pipe Size (mm)", "Head (m)"],
        # Long letter-spaced label without commas (dimension tables): still a label.
        ["Valve size inch (mm)", "C L A S S 8 0 0 G A T E V A L V E S", "A", "B"],
        ["Seat Test Pressure in kg/cm2 for every size up to 12 inch", "Class"],
    ],
)
def test_label_headers_are_not_captions(header: list[str]) -> None:
    assert not is_caption_header(header, MAX_LABEL_WORDS)


def test_caption_goes_out_and_label_row_becomes_header() -> None:
    rows = [
        _row("Model", "Power kW", "Pipe size", "Volts", "Head m"),
        _row("XA-1", "1.5", "50", "415", "20"),
    ]
    found = split_caption_header(CAPTION_HEADER, rows, MAX_LABEL_WORDS)
    assert found.caption is not None
    assert found.caption.startswith("PERFORMANCE CHART FOR")
    assert found.header == ["Model", "Power kW", "Pipe size", "Volts", "Head m"]
    assert _cells(found.rows) == [["XA-1", "1.5", "50", "415", "20"]]


def test_caption_without_label_row_leaves_placeholder_header() -> None:
    rows = [_row("S. No. 1 2", "XA-1 XA-2", "1.5 2.2", "50 65", "20 24")]
    found = split_caption_header(CAPTION_HEADER, rows, MAX_LABEL_WORDS)
    assert found.header == [f"Column {i}" for i in range(1, 6)]
    assert _cells(found.rows) == _cells(rows)


def test_series_row_under_single_caption_becomes_header() -> None:
    # Synthetic V3: "TECHNICAL DATA" over blank cells, series names as the first body row.
    header = ["TECHNICAL DATA", "TECHNICAL DATA", "Column 3", "Column 4"]
    rows = [
        _row("", "XA", "XB", "XC"),
        _row("Head Range", "Up to 70 m", "Up to 90 m", "Up to 20 m"),
    ]
    found = split_caption_header(header, rows, MAX_LABEL_WORDS)
    assert found.caption == "TECHNICAL DATA"
    assert found.header == ["TECHNICAL DATA", "XA", "XB", "XC"]
    assert _cells(found.rows) == [["Head Range", "Up to 70 m", "Up to 90 m", "Up to 20 m"]]


@pytest.mark.parametrize(
    ("header", "first"),
    [
        (["Model", "Head", "Flow"], ["XA-1", "20", "5"]),  # real header: untouched
        (["Parts", "Column 2"], ["Body", "Cast Iron"]),  # values, but only one label column
        (["Data", "Column 2", "Column 3"], ["Head", "Up to 70 m", "Up to 90 m"]),  # numbers
    ],
)
def test_headers_left_alone(header: list[str], first: list[str]) -> None:
    rows = [_row(*first)]
    found = split_caption_header(header, rows, MAX_LABEL_WORDS)
    assert (found.header, found.caption, _cells(found.rows)) == (header, None, [first])
