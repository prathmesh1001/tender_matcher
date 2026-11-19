"""A near miss needs a shared code or word, never only a bare number (B4)."""

import pytest

from app.ontology.loader import load_ontology_file
from app.schemas.enums import MatchStatus
from tests.unit.matching.test_comparator import ONTOLOGY_DIR, _compare, _prose

INSTRUMENTS = load_ontology_file(ONTOLOGY_DIR / "instruments.yaml")
VALVES = load_ontology_file(ONTOLOGY_DIR / "valves_pumps.yaml")
NPT_LISTING = "Process Connection: With SS316 Adjustable Connector having 1/2 inch NPT(M)"


@pytest.mark.parametrize(
    "line",
    [
        "Process Connection Size : 2” 1500#RTJ",  # 7865644 against a GeM listing
        "Process Connection : M33*2 (up to 40 Kg/cm2)",
    ],
)
def test_a_shared_bare_number_is_no_near_miss(line: str) -> None:
    result = _compare(line, [_prose(NPT_LISTING)], INSTRUMENTS)
    assert result.status is not MatchStatus.PARTIAL_MATCH


def test_a_flanged_size_is_not_met_by_a_screwed_half_inch() -> None:
    field = _prose('End Connection: 1/2" Screwed NPT')
    result = _compare('End Connection : 2" Flanged', [field], VALVES)
    assert result.status is not MatchStatus.PARTIAL_MATCH


@pytest.mark.parametrize(
    ("line", "printed"),
    [
        ("Seat MOC : SS316 + Stellite", "Seat: SS316"),  # a shared code
        ("End Connection : Flanged RF", "End Connection: Flanged FF"),  # a shared word
    ],
)
def test_a_shared_code_or_word_is_still_a_near_miss(line: str, printed: str) -> None:
    assert _compare(line, [_prose(printed)], VALVES).status is MatchStatus.PARTIAL_MATCH
