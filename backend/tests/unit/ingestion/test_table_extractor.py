from pathlib import Path

import pymupdf
import pytest

from app.ingestion.table_extractor import extract_page_tables, fill_merged_cells, normalize_cell
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


def test_fill_merged_cells_only_fills_none_not_blanks() -> None:
    rows: list[list[str | None]] = [["A", "1", ""], [None, "2", ""], [None, None, "x"]]
    assert fill_merged_cells(rows) == [["A", "1", ""], ["A", "2", ""], ["A", "2", "x"]]


def test_fill_merged_cells_none_in_first_row_becomes_blank() -> None:
    assert fill_merged_cells([[None, "1"]]) == [["", "1"]]
