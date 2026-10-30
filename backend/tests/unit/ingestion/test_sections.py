"""Grouping chunks into sections: runs, page ranges, hierarchy and section category."""

import pytest

from app.ingestion.sections import build_sections, section_category, section_number
from app.schemas.sections import CategorySource, ChunkCategory

DEFAULT = "other"


def _heading(category: str) -> ChunkCategory:
    return ChunkCategory(category=category, source=CategorySource.HEADING)


def _text(category: str) -> ChunkCategory:
    return ChunkCategory(category=category, source=CategorySource.TEXT)


def _default() -> ChunkCategory:
    return ChunkCategory(category=DEFAULT, source=CategorySource.DEFAULT)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("10.1 Following NDE shall be performed", "10.1"),
        ("3. Warranty", "3"),
        ("3.3) Identification &", "3.3"),
        ("(4) Inspection", "4"),
        ("48\N{RIGHT DOUBLE QUOTATION MARK} Class 150", None),
        ("Dimensions - PN 16", None),
        ("2026 pricing", None),
        (None, None),
    ],
)
def test_section_number(title: str | None, expected: str | None) -> None:
    assert section_number(title) == expected


def test_runs_of_one_heading_form_one_section_with_its_page_range() -> None:
    titles = [None, "Gate Valves", "Gate Valves", "Gate Valves", "Price Schedule"]
    pages = [1, 1, 2, 3, 3]
    cats = [_default(), *[_heading("technical_specifications")] * 3, _heading("costing")]
    sections = build_sections(titles, pages, cats, DEFAULT)
    assert [(s.title, s.start_page, s.end_page, s.chunk_indexes) for s in sections] == [
        (None, 1, 1, [0]),
        ("Gate Valves", 1, 3, [1, 2, 3]),
        ("Price Schedule", 3, 3, [4]),
    ]


def test_costing_next_to_technical_stays_separate() -> None:
    # Adjacent pages, different headings: never one section.
    titles = ["Technical Specification", "Price Schedule"]
    cats = [_heading("technical_specifications"), _heading("costing_and_commercial")]
    sections = build_sections(titles, [4, 5], cats, DEFAULT)
    assert [s.category for s in sections] == ["technical_specifications", "costing_and_commercial"]


def test_numbered_headings_hang_under_their_parent() -> None:
    titles = ["10. Inspection", "10.1 NDE", "10.2 Hydro test", "11. Warranty", "11.1 Period"]
    sections = build_sections(titles, [1] * 5, [_default()] * 5, DEFAULT)
    assert [s.parent_position for s in sections] == [None, 0, 0, None, 3]


def test_section_without_category_heading_takes_its_chunks_majority() -> None:
    members = [_text("costing_and_commercial"), _text("costing_and_commercial"), _default()]
    assert section_category(members, DEFAULT) == ("costing_and_commercial", CategorySource.CHUNKS)
    assert section_category([_default()], DEFAULT) == (DEFAULT, CategorySource.DEFAULT)
    assert section_category([_heading("warranty")], DEFAULT) == ("warranty", CategorySource.HEADING)


def test_repeated_heading_later_in_the_document_is_a_new_section() -> None:
    titles = ["Notes", "Gate Valves", "Notes"]
    sections = build_sections(titles, [1, 2, 3], [_default()] * 3, DEFAULT)
    assert [s.position for s in sections] == [0, 1, 2]
