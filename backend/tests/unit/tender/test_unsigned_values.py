"""Values in drawing notation (7865644 boq_spec.pdf variant tables) read as numbers."""

import pytest

from app.schemas.enums import Operator
from app.schemas.requirements import ValueKind
from app.tender.normalizer import parse_value, unsigned_value


@pytest.mark.parametrize(
    ("printed", "plain"),
    [
        ("U=280 MM", "280 MM"),
        ("U≤400 MM", "up to 400 MM"),
        ("≤ 400 mm", "up to 400 mm"),
        ("U≥50 mm", "at least 50 mm"),
        ("0 ≤ 1200 oC", "0 to 1200 oC"),
    ],
)
def test_drawing_notation_becomes_plain_words(printed: str, plain: str) -> None:
    assert unsigned_value(printed) == plain


@pytest.mark.parametrize(
    "printed", ["L1≤325mm, N1≤100mm", "1200 ≤ 0 oC", "SS 316 / 446", "M33*2 (up to 40 Kg/cm2)"]
)
def test_other_values_are_left_alone(printed: str) -> None:
    assert unsigned_value(printed) == printed


def test_upper_bound_is_at_most() -> None:
    parsed = parse_value("Immersion Length", "U≤400 MM", None, 3)
    assert (parsed.kind, parsed.operator, parsed.number, parsed.unit) == (
        ValueKind.NUMERIC,
        Operator.LTE,
        400.0,
        "mm",
    )


def test_bounds_are_a_range() -> None:
    parsed = parse_value("Range", "0 ≤ 1200 oC", None, 3)
    assert (parsed.operator, parsed.number, parsed.number_max) == (Operator.RANGE, 0.0, 1200.0)
