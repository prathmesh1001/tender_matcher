from pathlib import Path

import pymupdf
import pytest

from app.ingestion.table_cleanup import TableCleanupOptions
from app.ingestion.table_extractor import (
    GridCell,
    PageWords,
    completes_header,
    drop_overprints,
    extract_page_tables,
    fill_merged_cells,
    header_names,
    is_sub_header,
    item_starts,
    merge_continued_tables,
    merge_sub_header,
    normalize_cell,
    repeats_header,
    split_stacked_row,
)
from app.schemas.chunking import ExtractedTable, TableRow
from app.schemas.parsing import BBox
from tests.unit.ingestion.conftest import TABLE_HEADER, draw_table, write_text_pdf


def _tables(path: Path) -> list:  # type: ignore[type-arg]
    with pymupdf.open(path) as doc:
        return extract_page_tables(doc[0])


def test_header_and_rows_are_separated(catalogue_pdf: Path) -> None:
    [table] = _tables(catalogue_pdf)
    assert table.header == TABLE_HEADER
    assert table.page_number == 1
    assert table.index == 0
    assert len(table.rows) == 3


def test_cell_text_is_exact_decimals_and_spaces_survive(catalogue_pdf: Path) -> None:
    # PyMuPDF's Table.extract() gave "75\n." for 7.5 and "ETN50" for "ETN 50" on this PDF.
    [table] = _tables(catalogue_pdf)
    assert table.rows[0].cells == ["ETN 50", "120", "32", "7.5"]
    assert table.rows[2].cells == ["ETN 65", "200", "45", "15"]


def test_merged_model_cell_is_filled_down(catalogue_pdf: Path) -> None:
    [table] = _tables(catalogue_pdf)
    assert table.rows[1].cells == ["ETN 50", "150", "28", "7.5"]


def test_row_boxes_are_inside_the_table(catalogue_pdf: Path) -> None:
    [table] = _tables(catalogue_pdf)
    for row in table.rows:
        assert table.bbox.y0 <= row.bbox.y0 < row.bbox.y1 <= table.bbox.y1 + 0.5


def test_page_without_tables(tmp_path: Path) -> None:
    assert _tables(write_text_pdf(tmp_path / "t.pdf", [["Just prose here."]])) == []


def test_two_tables_on_one_page_are_indexed(tmp_path: Path) -> None:
    path = tmp_path / "two.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    draw_table(page, top=80)
    draw_table(page, top=300)
    doc.save(path)
    doc.close()
    tables = _tables(path)
    assert [t.index for t in tables] == [0, 1]
    assert tables[0].bbox.y0 < tables[1].bbox.y0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Head\n(m) ", "Head (m)"), ("7.5", "7.5"), ("", ""), (None, None)],
)
def test_normalize_cell(raw: str | None, expected: str | None) -> None:
    assert normalize_cell(raw) == expected


def _box(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _cell(text: str, x0: float, y0: float, x1: float, y1: float) -> GridCell:
    return GridCell(text=text, bbox=_box(x0, y0, x1, y1))


def _row_boxes(n: int, width: float = 300) -> list[BBox]:
    return [_box(0, 20 * i, width, 20 * (i + 1)) for i in range(n)]


def test_fill_merged_cells_only_fills_none_not_blanks() -> None:
    # Column 0 "A" spans rows 0-2 vertically; column 1 "2" spans rows 1-2; "" is a real blank.
    rows: list[list[GridCell | None]] = [
        [_cell("A", 0, 0, 100, 60), _cell("1", 100, 0, 200, 20), _cell("", 200, 0, 300, 20)],
        [None, _cell("2", 100, 20, 200, 60), _cell("", 200, 20, 300, 40)],
        [None, None, _cell("x", 200, 40, 300, 60)],
    ]
    assert fill_merged_cells(rows, _row_boxes(3)) == [
        ["A", "1", ""],
        ["A", "2", ""],
        ["A", "2", "x"],
    ]


def test_fill_merged_cells_none_in_first_row_becomes_blank() -> None:
    rows: list[list[GridCell | None]] = [[None, _cell("1", 100, 0, 200, 20)]]
    assert fill_merged_cells(rows, _row_boxes(1, width=200)) == [["", "1"]]


def test_horizontal_span_repeats_its_own_text_not_the_row_above() -> None:
    # C2: "Graphite" spans all grade columns; the shaft materials above must not leak into it.
    rows: list[list[GridCell | None]] = [
        [
            _cell("Shaft", 0, 0, 100, 20),
            _cell("SS410", 100, 0, 200, 20),
            _cell("SS316", 200, 0, 300, 20),
        ],
        [_cell("Packing", 0, 20, 100, 40), _cell("Graphite", 100, 20, 300, 40), None],
    ]
    assert fill_merged_cells(rows, _row_boxes(2)) == [
        ["Shaft", "SS410", "SS316"],
        ["Packing", "Graphite", "Graphite"],
    ]


def test_uncovered_continuation_is_blank() -> None:
    # The cell above does not reach down into this row, so nothing covers the gap.
    rows: list[list[GridCell | None]] = [
        [_cell("A", 0, 0, 100, 20), _cell("1", 100, 0, 200, 20)],
        [None, _cell("2", 100, 20, 200, 40)],
    ]
    assert fill_merged_cells(rows, _row_boxes(2, width=200)) == [["A", "1"], ["", "2"]]


def test_horizontal_span_in_a_real_pdf(tmp_path: Path) -> None:
    path = tmp_path / "span.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    cols, ys = [72, 172, 272, 372], [80, 100, 120, 140]
    for y in ys:
        page.draw_line((cols[0], y), (cols[-1], y))
    for x in (cols[0], cols[1], cols[3]):
        page.draw_line((x, ys[0]), (x, ys[-1]))
    page.draw_line((cols[2], ys[0]), (cols[2], ys[2]))  # no divider in the "Packing" row
    cells = {
        (0, 0): "Part",
        (0, 1): "Grade A",
        (0, 2): "Grade B",
        (1, 0): "Shaft",
        (1, 1): "SS410",
        (1, 2): "SS316",
        (2, 0): "Packing",
        (2, 1): "Graphite rings",
    }
    for (r, c), text in cells.items():
        page.insert_text((cols[c] + 4, ys[r] + 14), text, fontsize=9)
    doc.save(path)
    doc.close()
    [table] = _tables(path)
    assert table.rows[-1].cells == ["Packing", "Graphite rings", "Graphite rings"]


def _table(
    page: int, header: list[str], rows: list[list[str]], y0: float, y1: float
) -> ExtractedTable:
    step = (y1 - y0) / (len(rows) + 1)
    return ExtractedTable(
        page_number=page,
        index=0,
        bbox=_box(50, y0, 550, y1),
        header=header,
        rows=[
            TableRow(cells=cells, bbox=_box(50, y0 + step * (i + 1), 550, y0 + step * (i + 2)))
            for i, cells in enumerate(rows)
        ],
    )


HEADER = ["Parameter", "Value"]
HEIGHTS = {1: 800.0, 2: 800.0, 3: 800.0}


def test_continuation_without_header_takes_the_previous_header() -> None:
    # C1: page 2 starts with data; PyMuPDF took the first data row as the header.
    p1 = _table(1, HEADER, [["Type", "Monoblock pump"]], 500, 780)
    p2 = _table(2, ["Head", "14.0 (meter)"], [["Power", "2.0 (horsepower)"]], 40, 200)
    merged = merge_continued_tables({1: [p1], 2: [p2]}, HEIGHTS, 0.25)
    cont = merged[2][0]
    assert cont.header == HEADER
    assert [r.cells for r in cont.rows] == [["Head", "14.0 (meter)"], ["Power", "2.0 (horsepower)"]]
    assert cont.continued_from_page == 1
    assert cont.page_number == 2


def test_repeated_header_is_reused_not_duplicated() -> None:
    p1 = _table(1, HEADER, [["Type", "Monoblock"]], 500, 780)
    p2 = _table(2, HEADER, [["Parameter", "Value"], ["Head", "14 m"]], 40, 200)
    cont = merge_continued_tables({1: [p1], 2: [p2]}, HEIGHTS, 0.25)[2][0]
    assert [r.cells for r in cont.rows] == [["Head", "14 m"]]


def test_continuation_chains_over_three_pages() -> None:
    p1 = _table(1, HEADER, [["A", "1"]], 500, 780)
    p2 = _table(2, HEADER, [["B", "2"]], 40, 780)
    p3 = _table(3, ["C", "3"], [["D", "4"]], 40, 200)
    merged = merge_continued_tables({1: [p1], 2: [p2], 3: [p3]}, HEIGHTS, 0.25)
    assert merged[3][0].header == HEADER
    assert merged[3][0].continued_from_page == 1


@pytest.mark.parametrize(
    ("p1_bottom", "p2_top", "p2_header"),
    [
        (400, 40, ["Head", "14 m"]),  # page 1 table ends mid-page
        (780, 500, ["Head", "14 m"]),  # page 2 table starts mid-page
        (780, 40, ["A", "B", "C"]),  # different column count
    ],
)
def test_unrelated_tables_are_not_merged(
    p1_bottom: float, p2_top: float, p2_header: list[str]
) -> None:
    p1 = _table(1, HEADER, [["Type", "Monoblock"]], 300, p1_bottom)
    p2 = _table(2, p2_header, [["x"] * len(p2_header)], p2_top, p2_top + 150)
    merged = merge_continued_tables({1: [p1], 2: [p2]}, HEIGHTS, 0.25)
    assert merged[2][0] == p2


def test_spanned_header_repeats_its_label_and_blank_header_is_numbered() -> None:
    assert header_names(["No.", "Materials", None, None, ""]) == [
        "No.",
        "Materials",
        "Materials",
        "Materials",
        "Column 5",
    ]


def test_two_row_header_is_merged() -> None:
    # C3: "Materials" spans the grade columns; the next row names each grade.
    raw: list[str | None] = ["No.", "Components", "Materials", None, None]
    sub = ["", "", "WCB/ WCC", "LCB/ LCC", "C5"]
    assert is_sub_header(raw, sub)
    assert merge_sub_header(header_names(raw), sub) == [
        "No.",
        "Components",
        "Materials WCB/ WCC",
        "Materials LCB/ LCC",
        "Materials C5",
    ]


@pytest.mark.parametrize(
    ("raw", "row"),
    [
        (["No.", "Components", "Materials", None], ["1", "Body", "WCB", "LCB"]),  # data row
        (["Model", "Flow", "Head"], ["", "m3/h", "m"]),  # no spanned header
        (["No.", "Materials", None], ["", "WCB", ""]),  # gap under the span
    ],
)
def test_data_rows_are_not_mistaken_for_sub_headers(raw: list[str | None], row: list[str]) -> None:
    assert not is_sub_header(raw, row)


def _bid_table_pdf(path: Path, title: bool) -> Path:
    """Two-column label/value table like a GeM bid document, optionally under a title row."""
    doc = pymupdf.open()
    page = doc.new_page()
    cols, ys = [72, 222, 372], [80, 100, 120, 140]
    for y in ys:
        page.draw_line((cols[0], y), (cols[-1], y))
    for x in (cols[0], cols[2]):
        page.draw_line((x, ys[0]), (x, ys[-1]))
    page.draw_line((cols[1], ys[1] if title else ys[0]), (cols[1], ys[-1]))
    rows = [("Bid End Date/Time", "16-10-2026 15:00:00"), ("EMD Amount", "24000")]
    if title:
        page.insert_text((cols[0] + 4, ys[0] + 14), "Bid Details", fontsize=9)
    else:
        rows.insert(0, ("Advisory Bank", "State Bank of India"))
    for r, (label, value) in enumerate(rows, start=1 if title else 0):
        page.insert_text((cols[0] + 4, ys[r] + 14), label, fontsize=9)
        page.insert_text((cols[1] + 4, ys[r] + 14), value, fontsize=9)
    doc.save(path)
    doc.close()
    return path


def test_key_value_table_title_is_not_a_header(tmp_path: Path) -> None:
    path = _bid_table_pdf(tmp_path / "bid.pdf", title=True)
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(doc[0], key_value_pairs=True)
    assert table.key_value
    assert table.title == "Bid Details"
    assert [r.cells for r in table.rows] == [
        ["Bid End Date/Time", "16-10-2026 15:00:00"],
        ["EMD Amount", "24000"],
    ]


def test_key_value_table_first_pair_is_kept_as_a_row(tmp_path: Path) -> None:
    path = _bid_table_pdf(tmp_path / "bid.pdf", title=False)
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(doc[0], key_value_pairs=True)
    assert table.title is None
    assert table.rows[0].cells == ["Advisory Bank", "State Bank of India"]
    assert len(table.rows) == 3


def _stacked_items_pdf(path: Path) -> Path:
    """Spec table whose items 10 and 20 share one ruled row; items 30 and 40 are ruled.

    PyMuPDF only detects ruled grids of about 4 x 4 cells, hence the extra rows and column.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    cols, ys = [72, 122, 322, 392, 462], [80, 100, 220, 240, 260]
    for y in ys:
        page.draw_line((cols[0], y), (cols[-1], y))
    for x in cols:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for c, label in enumerate(["Item No", "Description", "Qty", "Unit"]):
        page.insert_text((cols[c] + 4, ys[0] + 14), label, fontsize=9)
    items = [
        (112, "10", ["VALVE 250MM", "CLASS 150", "TORQUE 820 Nm"], "3"),
        (160, "20", ["VALVE 300MM"], "6"),
        (234, "30", ["GASKET"], "9"),
        (254, "40", ["BOLT"], "12"),
    ]
    for top, number, lines, qty in items:
        page.insert_text((cols[0] + 4, top), number, fontsize=9)
        page.insert_text((cols[2] + 4, top), qty, fontsize=9)
        page.insert_text((cols[3] + 4, top), "EA", fontsize=9)
        for i, line in enumerate(lines):
            page.insert_text((cols[1] + 4, top + i * 12), line, fontsize=9)
    doc.save(path)
    doc.close()
    return path


def test_stacked_line_items_are_split_into_rows(tmp_path: Path) -> None:
    path = _stacked_items_pdf(tmp_path / "spec.pdf")
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(doc[0], key_value_pairs=True, split_stacked=True)
    assert [r.cells for r in table.rows] == [
        ["10", "VALVE 250MM CLASS 150 TORQUE 820 Nm", "3", "EA"],
        ["20", "VALVE 300MM", "6", "EA"],
        ["30", "GASKET", "9", "EA"],
        ["40", "BOLT", "12", "EA"],
    ]
    assert table.rows[0].bbox.y1 <= table.rows[1].bbox.y0


def test_stacked_rows_are_kept_whole_unless_asked(tmp_path: Path) -> None:
    path = _stacked_items_pdf(tmp_path / "spec.pdf")
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(doc[0])
    assert [r.cells[0] for r in table.rows] == ["10 20", "30", "40"]


@pytest.mark.parametrize(
    ("anchor", "others", "expected"),
    [
        ([(10.0, "10"), (50.0, "20")], [[10.0, 22.0, 50.0]], [10.0, 50.0]),
        ([(10.0, "1.1"), (50.0, "1.2")], [[10.5, 50.5]], [10.0, 50.0]),
        ([(10.0, "10")], [[10.0, 50.0]], []),  # one item only
        ([(10.0, "Gate"), (50.0, "valve")], [[10.0, 50.0]], []),  # wrapped words, not ids
        ([(10.0, "10"), (50.0, "20")], [[10.0, 30.0]], []),  # no other column aligns
    ],
)
def test_item_starts(
    anchor: list[tuple[float, str]], others: list[list[float]], expected: list[float]
) -> None:
    assert item_starts(anchor, others) == expected


def test_rows_repeating_the_header_are_dropped() -> None:
    header = ["Item No", "Material Code", "Unit"]
    assert repeats_header(header, ["Item No", "Material Code", "Unit"])
    assert repeats_header(header, ["Item No", "", "Unit"])
    assert not repeats_header(header, ["10", "2190", "EA"])
    assert not repeats_header(header, ["", "", ""])


def test_catalogue_tables_keep_their_header(tmp_path: Path) -> None:
    path = _bid_table_pdf(tmp_path / "cat.pdf", title=False)
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(doc[0])
    assert not table.key_value
    assert table.header == ["Advisory Bank", "State Bank of India"]


def test_thin_rows_do_not_take_text_from_the_next_row(tmp_path: Path) -> None:
    """Cells keep only words centred inside them: no "01 02 | Body Body Seat Ring" bleed."""
    path = tmp_path / "parts.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    cols, ys = [72, 122, 272, 422, 522], [80, 92, 104, 116, 128]
    for y in ys:
        page.draw_line((cols[0], y), (cols[-1], y))
    for x in cols:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    rows = [
        ("No.", "Part", "Material", "Note"),
        ("01", "Body", "A216 WCB", "-"),
        ("02", "Body Seat Ring", "A105 + HF", "-"),
        ("03", "Disc", "A216 WCB", "-"),
    ]
    for r, cells in enumerate(rows):
        for c, text in enumerate(cells):
            page.insert_text((cols[c] + 3, ys[r] + 10.5), text, fontsize=10)
    doc.save(path)
    doc.close()
    [table] = _tables(path)
    assert [r.cells[:3] for r in table.rows] == [
        ["01", "Body", "A216 WCB"],
        ["02", "Body Seat Ring", "A105 + HF"],
        ["03", "Disc", "A216 WCB"],
    ]


@pytest.mark.parametrize(
    ("header", "row", "expected"),
    [
        (["Column 1", "Column 2", "Description", "Qty"], ["Item No", "Code", "", ""], True),
        (["Column 1", "Description"], ["10", ""], False),  # a number: data, not a label
        (["Column 1", "Description"], ["Item No", "Valve"], False),  # clashes with a label
        (["Item No", "Description"], ["10", "Valve"], False),  # no blank header cells
    ],
)
def test_completes_header(header: list[str], row: list[str], expected: bool) -> None:
    assert completes_header(header, row) is expected


def test_unsplittable_stack_is_kept_whole_and_flagged() -> None:
    """Item numbers that start outside the row cannot be split safely: flag, never guess."""
    words = PageWords(
        words=(
            (76.0, 84.0, 82.0, 92.0, "3", 0, 0, 0),
            (76.0, 100.0, 82.0, 108.0, "4", 0, 1, 0),
            (126.0, 84.0, 150.0, 92.0, "Stem", 1, 0, 0),
            (126.0, 100.0, 150.0, 108.0, "Disc", 1, 1, 0),
        )
    )
    cells = [
        GridCell(text="3 4", bbox=BBox(x0=72, y0=80, x1=122, y1=112)),
        GridCell(text="Stem Disc", bbox=BBox(x0=122, y0=80, x1=272, y1=112)),
    ]
    [row] = split_stacked_row(words, cells, BBox(x0=72, y0=98, x1=272, y1=112))
    assert row.stacked_unresolved
    assert [c.text for c in row.cells if c] == ["3 4", "Stem Disc"]


def test_two_numbers_on_one_line_are_not_a_stack() -> None:
    """ "1000 410" side by side comes from merged columns, not stacked items: no flag."""
    words = PageWords(
        words=(
            (76.0, 84.0, 90.0, 92.0, "1000", 0, 0, 0),
            (94.0, 84.0, 108.0, 92.0, "410", 0, 0, 1),
            (126.0, 84.0, 150.0, 92.0, "770", 1, 0, 0),
        )
    )
    cells = [
        GridCell(text="1000 410", bbox=BBox(x0=72, y0=80, x1=122, y1=96)),
        GridCell(text="770", bbox=BBox(x0=122, y0=80, x1=272, y1=96)),
    ]
    [row] = split_stacked_row(words, cells, BBox(x0=72, y0=80, x1=272, y1=96))
    assert not row.stacked_unresolved


def test_overprinted_words_are_read_once() -> None:
    words = drop_overprints(
        [
            (229.6, 680.6, 242.9, 690.1, "250", 34, 1, 0),
            (229.6, 679.2, 242.9, 688.7, "250", 59, 1, 0),  # fake-bold copy
            (260.0, 680.6, 270.0, 690.1, "250", 34, 1, 1),  # a real second 250
        ]
    )
    assert [(w[0], w[4]) for w in words] == [(229.6, "250"), (260.0, "250")]


def _grid_pdf(path: Path, rows: list[list[str]]) -> Path:
    """Synthetic ruled grid: one row per list, 3 columns, text in each cell."""
    doc = pymupdf.open()
    page = doc.new_page()
    cols = [72, 200, 300, 400]
    ys = [80 + 20 * i for i in range(len(rows) + 1)]
    for y in ys:
        page.draw_line((cols[0], y), (cols[-1], y))
    for x in cols:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if cell:
                page.insert_text((cols[j] + 4, ys[i] + 14), cell, fontsize=9)
    doc.save(path)
    doc.close()
    return path


SPLIT_ROWS = [
    ["Technical data", "Series A", "Series B"],
    ["Pressure", "Up to 16", "bar"],
    ["Temperature", "-30 C to", "+140 C"],
    ["Head", "Up to 50 m", "Up to 40 m"],
]


def test_value_split_across_columns_is_rejoined(tmp_path: Path) -> None:
    # Synthetic V5: "Up to 16 bar" printed across two series columns, cut by the column line.
    path = _grid_pdf(tmp_path / "split.pdf", SPLIT_ROWS)
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(
            doc[0], cleanup=TableCleanupOptions(units=frozenset({"bar", "c"}))
        )
    assert [r.cells for r in table.rows] == [
        ["Pressure", "Up to 16 bar", "Up to 16 bar"],
        ["Temperature", "-30 C to +140 C", "-30 C to +140 C"],
        ["Head", "Up to 50 m", "Up to 40 m"],
    ]
    assert [r.spanning_columns for r in table.rows] == [(1, 2), (1, 2), ()]


def test_split_values_are_kept_without_units(tmp_path: Path) -> None:
    # Without unit spellings only the open range ("... to" | "+140 C") is re-joined.
    path = _grid_pdf(tmp_path / "split.pdf", SPLIT_ROWS)
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(doc[0])
    assert table.rows[0].cells == ["Pressure", "Up to 16", "bar"]
    assert table.rows[1].cells == ["Temperature", "-30 C to +140 C", "-30 C to +140 C"]
