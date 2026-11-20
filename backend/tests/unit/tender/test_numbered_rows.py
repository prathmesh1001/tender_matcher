"""Item descriptions from the spec table rows numbered like the items (8210284 boq_spec.pdf)."""

import pytest

from app.tender.normalizer import title_size
from app.tender.numbered_rows import TableRow, description_lines, rows_for_items

KNIFE = [
    "80MM DIAMETER KNIFE GATE VALVE",
    "VALVE TYPE: BI-DIRECTIONAL KNIFE EDGE GATE VALVE",
    "END STYLE: LUGGED",
    "BODY: CAST IRON IS210GR.FG260",
    "RATING: PN16",
    "GLAND PACKING: PTFE IMPREGNATED SYNTAX: MAX. TEMP 232°C, MAX",
    "PRESSURE 150 PSI",
]


def _row(number: str, lines: list[str], document: str = "boq_spec.pdf") -> TableRow:
    cells = [number, "", " ".join(lines), "NO"]
    return TableRow(document, 1, cells, [[number], [], lines, ["NO"]])


def test_a_line_opening_with_a_label_starts_a_new_line() -> None:
    assert description_lines(KNIFE) == [
        "80MM DIAMETER KNIFE GATE VALVE",
        "VALVE TYPE: BI-DIRECTIONAL KNIFE EDGE GATE VALVE",
        "END STYLE: LUGGED",
        "BODY: CAST IRON IS210GR.FG260",
        "RATING: PN16",
        "GLAND PACKING: PTFE IMPREGNATED SYNTAX: MAX. TEMP 232°C, MAX PRESSURE 150 PSI",
    ]


def test_wrapped_lines_join_the_line_before() -> None:
    printed = ['GATE VALVE 3" (80MM) WITH TWO FLANGES, TWO GASKET, NUTS AND', "BOLTS, BONNETED"]
    assert description_lines(printed) == [
        'GATE VALVE 3" (80MM) WITH TWO FLANGES, TWO GASKET, NUTS AND BOLTS, BONNETED'
    ]


def test_each_item_takes_the_row_with_its_number() -> None:
    rows = [_row("1", KNIFE), _row("2", ['GATE VALVE 3" WITH TWO FLANGES, TWO GASKETS'])]
    found = rows_for_items(["1", "2"], rows, 5)
    assert found["1"].lines[2] == "END STYLE: LUGGED"
    assert found["2"].description.startswith('GATE VALVE 3"')


@pytest.mark.parametrize(
    "rows",
    [
        [_row("1", KNIFE)],  # item 2 has no row
        [_row("1", KNIFE), _row("2", KNIFE), _row("3", KNIFE)],  # a row no item has
        [_row("1", KNIFE), _row("1", KNIFE), _row("2", KNIFE)],  # a number twice
        [_row("1", ["NO"]), _row("2", ["NO"])],  # no description: a unit or price column
        # two files fit: never guessed
        [_row("1", KNIFE), _row("2", KNIFE), _row("1", KNIFE, "a.pdf"), _row("2", KNIFE, "a.pdf")],
    ],
)
def test_rows_that_do_not_number_every_item_once_give_nothing(rows: list[TableRow]) -> None:
    assert rows_for_items(["1", "2"], rows, 5) == {}


@pytest.mark.parametrize(("title", "size"), [
    ("BALL VALVE 40MM", "40mm"),
    ("80MM BUTTERFLY VALVE", "80mm"),
    ("AUDCO SLIMSEAL BUTTERFLY VALVE 100MM", "100mm"),
    ("CRYOGENIC GLOBE VALVE STEM LENGTH 220 MM", None),  # a dimension, not the size
    ("VALVE 50MM AND 80MM", None),  # two sizes: never guessed
    ("GLOBE VALVE 25 MM SS", None),  # mid-title millimetres may measure anything
])  # fmt: skip
def test_millimetres_at_a_title_edge_are_its_size(title: str, size: str | None) -> None:
    assert title_size(title) == size
