from pathlib import Path

import pymupdf
import pytest

from app.ingestion.table_extractor import (
    GridCell,
    extract_page_tables,
    fill_merged_cells,
    merge_continued_tables,
    normalize_cell,
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
