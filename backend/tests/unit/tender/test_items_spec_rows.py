"""GeM category specification rows read as requirements of a single nearly empty item."""

import pytest

from app.tender.items import spec_row_line

LABELS = ("Specification Name", "Bid Requirement (Allowed Values)")


@pytest.mark.parametrize(
    ("text", "line"),
    [
        (
            "Specification: Performance Parameters; Specification Name: Head; "
            "Bid Requirement (Allowed Values): 14.0 (meter)",
            "Head : 14.0 (meter)",
        ),
        (
            "ववरण/Specification: Performance Parameters; "
            "विश T का नाम /Specification Name: Capacity, liter/hour; "
            "बड के िलए /Bid Requirement (Allowed Values): 16000",
            "Capacity liter/hour : 16000",  # the comma would split one name into two
        ),
    ],
)
def test_a_spec_row_reads_as_name_and_value(text: str, line: str) -> None:
    assert spec_row_line(text, LABELS) == line


@pytest.mark.parametrize(
    "text",
    [
        "Specification Name: Name; Bid Requirement (Allowed Values): (Allowed Values)",  # header
        "Specification Name: Column 1 विश; Bid Requirement (Allowed Values): Column 1 बड",  # header
        "Performance security shall not be released till damages are recovered.",  # ATC prose
        "Bill of Quantities and Technical Specification",
    ],
)
def test_other_lines_are_not_spec_rows(text: str) -> None:
    assert spec_row_line(text, LABELS) is None


def test_without_labels_nothing_is_a_spec_row() -> None:
    assert (
        spec_row_line("Specification Name: Head; Bid Requirement (Allowed Values): 14", ("", ""))
        is None
    )
