"""Units stated in a field label or spelled out (GeM step D). Values shaped like real GeM rows."""

import pytest

from app.matching.values import quantities
from app.ontology.loader import load_ontology_file
from app.schemas.enums import MatchStatus
from tests.unit.matching.test_comparator import ONTOLOGY_DIR, _compare, _prose

INSTRUMENTS = load_ontology_file(ONTOLOGY_DIR / "instruments.yaml")
VALVES = load_ontology_file(ONTOLOGY_DIR / "valves_pumps.yaml")


@pytest.mark.parametrize(
    ("line", "printed"),
    [
        ("Size : DN 100", "Valve Size (mm): 100"),  # valve: unit only in the label
        ("Head : 30 m", "Total Head (in meter): 30"),  # pump
        ("Motor Power : 3.7 kW", "Motor Rating (kW): 3.7"),
        ("Size : 100 mm", "Valve Size: 100 millimeter"),  # unit spelled out
    ],
)
def test_valve_and_pump_units_from_the_label_or_spelled_out(line: str, printed: str) -> None:
    assert _compare(line, [_prose(printed)], VALVES).status is MatchStatus.FULL_MATCH


@pytest.mark.parametrize(
    ("line", "printed"),
    [
        ("Sheath diameter : 6 mm", "Sheath OD (in mm): 6"),  # real GeM RTD listing label
        ("Sheath diameter : 6 mm", "Outer Diameter: 6 millimeter"),  # real thermocouple row
        ("Thermowell length : 280 mm", "Thermowell length (mm): 280"),
    ],
)
def test_instrument_units_from_the_label_or_spelled_out(line: str, printed: str) -> None:
    assert _compare(line, [_prose(printed)], INSTRUMENTS).status is MatchStatus.FULL_MATCH


def test_a_label_unit_still_fails_a_different_number() -> None:
    result = _compare("Size : DN 150", [_prose("Valve Size (mm): 100")], VALVES)
    assert result.status is MatchStatus.MISSING_CAPABILITY


def test_a_bracket_that_is_no_unit_gives_a_bare_number_no_unit() -> None:
    # B4: "(Nominal)" is not a unit, so "100" stays a bare number and decides nothing.
    result = _compare("Size : DN 100", [_prose("Valve Size (Nominal): 100")], VALVES)
    assert result.status is MatchStatus.UNSUPPORTED


def test_a_printed_unit_wins_over_the_label_unit() -> None:
    result = _compare("Head : 30 m", [_prose("Total Head (in meter): 3000 cm")], VALVES)
    assert result.status is MatchStatus.FULL_MATCH


def test_a_motor_sold_in_hp_meets_its_nominal_kw() -> None:
    # 5 hp = 3.73 kW, listed as a 3.7 kW motor; the power attribute allows 2 %.
    result = _compare("Motor Power : 3.7 kW", [_prose("Motor Rating: 5 horsepower")], VALVES)
    assert result.status is MatchStatus.FULL_MATCH


def test_the_nominal_tolerance_does_not_hide_a_smaller_motor() -> None:
    result = _compare("Motor Power : 5.5 kW", [_prose("Motor Rating: 5 horsepower")], VALVES)
    assert result.status is MatchStatus.MISSING_CAPABILITY


def test_a_multi_word_unit_after_a_number_is_one_unit() -> None:
    found = quantities("0-600 deg Celsius", ["pn"], ["deg celsius", "degree celsius"])
    assert [(q.low, q.high, q.unit) for q in found] == [(0.0, 600.0, "degcelsius")]
    assert quantities("400 degree Celsius", ["pn"], ["degree celsius"])[0].unit == (
        "degreecelsius"
    )
