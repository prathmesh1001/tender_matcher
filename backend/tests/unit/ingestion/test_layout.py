"""Rule-grid tables rebuilt from drawn rules (failure case T1). All geometry is synthetic."""

from pathlib import Path
from typing import ClassVar

import pymupdf
import pytest

from app.ingestion.layout import (
    LayoutOptions,
    Rule,
    build_grid,
    cell_box,
    cluster_positions,
    connected_rules,
    grid_cells,
    page_grids,
    rule_from_box,
)
from app.ingestion.table_extractor import extract_page_tables, is_header_band, rule_grid_tables
from app.schemas.parsing import BBox

OPTIONS = LayoutOptions(
    rule_max_thickness=2.0, rule_tolerance=2.0, min_columns=2, min_rows=3, max_rules=2000
)


def _h(y: float, x0: float, x1: float) -> Rule:
    return Rule(horizontal=True, at=y, start=x0, end=x1)


def _v(x: float, y0: float, y1: float) -> Rule:
    return Rule(horizontal=False, at=x, start=y0, end=y1)


# A 3-column, 4-band grid. Column 2 has no rule between bands 1-2 and 2-3, so its cell from
# y=20 to y=50 is shared by two item rows (a specification for several items).
XS = (0.0, 20.0, 100.0, 200.0)
GRID_RULES = [
    _v(0, 0, 50),
    _v(20, 0, 50),
    _v(100, 0, 50),
    _v(200, 0, 50),
    _h(0, 0, 200),
    _h(10, 0, 200),
    _h(20, 0, 200),
    _h(35, 0, 100),  # stops at column 2: rows 2 and 3 share the specification cell
    _h(50, 0, 200),
]


# ---------------------------------------------------------------- rules


def test_thin_rectangles_are_rules() -> None:
    assert rule_from_box(BBox(x0=10, y0=50, x1=300, y1=50.7), 2.0) == _h(50.35, 10, 300)
    assert rule_from_box(BBox(x0=70, y0=20, x1=70.8, y1=500), 2.0) == _v(70.4, 20, 500)


def test_areas_are_not_rules() -> None:
    assert rule_from_box(BBox(x0=0, y0=0, x1=100, y1=40), 2.0) is None  # cell shading
    assert rule_from_box(BBox(x0=0, y0=0, x1=1, y1=1), 2.0) is None  # a dot


def test_cluster_positions_merges_close_values() -> None:
    assert cluster_positions([10.0, 10.5, 30.0, 11.0, 29.0], 2.0) == [10.0, 29.0]


def test_page_border_is_not_joined_to_a_table() -> None:
    border = [_h(5, 5, 600), _h(800, 5, 600), _v(5, 5, 800), _v(600, 5, 800)]
    table = [_h(100 + y, 100, 300) for y in (0, 10, 20, 30)] + [
        _v(x, 100, 130) for x in (100, 200, 300)
    ]
    components = connected_rules(border + table, 2.0)
    assert sorted(len(c) for c in components) == [4, 7]


def test_collinear_pieces_join() -> None:
    assert len(connected_rules([_h(10, 0, 50), _h(10.5, 51, 100)], 2.0)) == 1
    assert len(connected_rules([_h(10, 0, 50), _h(10, 80, 100)], 2.0)) == 2


# ---------------------------------------------------------------- grid


def test_grid_needs_enough_rows_and_columns() -> None:
    assert build_grid([_h(0, 0, 10), _h(10, 0, 10), _v(0, 0, 10), _v(10, 0, 10)], OPTIONS) is None
    grid = build_grid(GRID_RULES, OPTIONS)
    assert grid is not None
    assert grid.xs == XS
    assert grid.ys == (0, 10, 20, 35, 50)


def test_too_many_rules_is_a_drawing() -> None:
    hatch = [_h(float(y), 0, 100) for y in range(2001)] + [_v(0, 0, 2000), _v(100, 0, 2000)]
    assert page_grids(hatch, OPTIONS) == []


def test_merged_cell_covers_every_row_it_spans() -> None:
    grid = build_grid(GRID_RULES, OPTIONS)
    assert grid is not None
    spec_rows_2_and_3 = BBox(x0=100, y0=20, x1=200, y1=50)
    assert cell_box(grid, 2, 2) == spec_rows_2_and_3
    assert cell_box(grid, 3, 2) == spec_rows_2_and_3
    assert cell_box(grid, 2, 1) == BBox(x0=20, y0=20, x1=100, y1=35)  # own description
    assert cell_box(grid, 3, 1) == BBox(x0=20, y0=35, x1=100, y1=50)


def test_cell_without_vertical_rule_spans_columns() -> None:
    inner = {_v(20, 0, 50), _v(100, 0, 50)}
    rules = [r for r in GRID_RULES if r not in inner] + [_v(20, 10, 50), _v(100, 10, 50)]
    grid = build_grid(rules, OPTIONS)
    assert grid is not None
    title = BBox(x0=0, y0=0, x1=200, y1=10)
    assert grid_cells(grid)[0] == [title, title, title]


# ---------------------------------------------------------------- tables from a PDF


def _rule_table_pdf(path: Path) -> Path:
    """Synthetic tender BOQ drawn with thin filled rectangles, no stroked lines.

    Items 2 and 3 share one specification cell (no rule between them in that column).
    """
    doc = pymupdf.open()
    page = doc.new_page()
    xs, ys = [72, 102, 252, 452], [80, 100, 120, 140, 160, 180]
    black = (0, 0, 0)
    for i, x in enumerate(xs):
        top = ys[0] if i in (0, len(xs) - 1) else ys[1]  # the title row spans all columns
        page.draw_rect(pymupdf.Rect(x, top, x + 0.7, ys[-1]), color=None, fill=black)
    for i, y in enumerate(ys):
        right = xs[2] if i == 4 else xs[-1]  # rows 3-4 share the specification cell
        page.draw_rect(pymupdf.Rect(xs[0], y, right, y + 0.7), color=None, fill=black)
    texts = [
        (0, 0, "Specification"),
        (1, 0, "Sl. No."), (1, 1, "Description"), (1, 2, "Technical specification"),
        (2, 0, "1"), (2, 1, "GATE VALVE DN50"), (2, 2, "Body: cast iron"),
        (3, 0, "2"), (3, 1, "BALL VALVE DN20"), (3, 2, "Class 800, screwed ends"),
        (4, 0, "3"), (4, 1, "BALL VALVE DN25"),
    ]  # fmt: skip
    for row, col, text in texts:
        page.insert_text((xs[col] + 3, ys[row] + 14), text, fontsize=8)
    doc.save(path)
    doc.close()
    return path


class _NoTables:
    tables: ClassVar[list[object]] = []


def _finder_sees_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make PyMuPDF's finder return nothing, as it does on the real T1 page."""
    monkeypatch.setattr(pymupdf.Page, "find_tables", lambda self, **_: _NoTables())


def test_table_drawn_with_thin_rectangles_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _rule_table_pdf(tmp_path / "boq.pdf")
    _finder_sees_nothing(monkeypatch)
    with pymupdf.open(path) as doc:
        [table] = extract_page_tables(doc[0], key_value_pairs=True, layout=OPTIONS)
    assert table.title == "Specification"
    assert table.header == ["Sl. No.", "Description", "Technical specification"]
    assert [r.cells for r in table.rows] == [
        ["1", "GATE VALVE DN50", "Body: cast iron"],
        ["2", "BALL VALVE DN20", "Class 800, screwed ends"],
        ["3", "BALL VALVE DN25", "Class 800, screwed ends"],  # shared cell, not item 2's only
    ]


def test_rule_grids_are_off_without_layout_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _rule_table_pdf(tmp_path / "boq.pdf")
    _finder_sees_nothing(monkeypatch)
    with pymupdf.open(path) as doc:
        assert extract_page_tables(doc[0], key_value_pairs=True) == []


def test_rule_grid_tables_reads_the_page_directly(tmp_path: Path) -> None:
    path = _rule_table_pdf(tmp_path / "boq.pdf")
    with pymupdf.open(path) as doc:
        [table] = rule_grid_tables(doc[0], key_value_pairs=True, layout=OPTIONS)
    assert len(table.rows) == 3


@pytest.mark.parametrize("thickness", [0.5, 1.5])
def test_rule_thickness_threshold(thickness: float) -> None:
    box = BBox(x0=0, y0=0, x1=100, y1=thickness)
    assert rule_from_box(box, 2.0) is not None
    assert rule_from_box(box, thickness / 2) is None


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["Sl. No.", "Description", "Technical specification"], True),
        (["1.", "Tender Reference No.", "REF-0001"], False),  # numbered data row
        (
            ["Clause", "THE BIDDER SHALL UPLOAD THE INVOICE DETAILS ON THE PORTAL WITHIN DAYS"],
            False,
        ),
        (["Description", ""], False),  # one label alone
    ],
)
def test_header_band(texts: list[str], expected: bool) -> None:
    assert is_header_band(texts, max_words=8) is expected
