"""GeM category specification rows read as requirements of a single nearly empty item."""

import pytest

from app.tender.items import quantity_header, spec_row_line

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
        (
            # 8188046 p4: the value header kept only its Hindi part at a page break
            "(Hindustan Petroleum Corporation Limited) | ववरण/Specification: GENERIC; "
            "विश V का नाम /Specification Name: Conformity to Governing Specification (ISS); "
            "बड के िलए: IS 13095",
            "Conformity to Governing Specification (ISS) : IS 13095",
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


TITLE = "Butterfly Valves (V2) as per IS 13095 (Q2)"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Butterfly Valves (V2) As Per IS 13095 ( 70 pieces )", ("70", "pieces")),
        (
            "Butterfly Valves (V2) As Per IS 13095 ( 25 pieces )\nBis Required\nYes",
            ("25", "pieces"),
        ),
        (TITLE, None),  # the Item Category row itself states no quantity
        ("Gate Valves ( 10 pieces )", None),  # another product's title
        ("Butterfly Valves (V2) as per IS 13095 is required", None),
    ],
)
def test_a_quantity_line_opens_an_items_block(text: str, found: tuple[str, str] | None) -> None:
    assert quantity_header(text, TITLE) == found
