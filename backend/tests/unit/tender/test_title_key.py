"""A product key for items whose title names no ontology product type (GeM step C)."""

from pathlib import Path

import pytest

from app.ontology.loader import load_ontologies
from app.tender.title_key import TitleKeyOptions, key_phrase, title_key

ONTOLOGIES = load_ontologies(Path(__file__).resolve().parents[3] / "ontology")
OPTIONS = TitleKeyOptions(
    stop_words=frozenset({"supply", "of", "for", "the", "type", "nos", "with", "and"}),
    max_words=3,
)


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("PVC foot valve Ball type", "foot_valve"),  # 8157758 item 21: an ontology other product
        ("Y-Strainer 50 NB flanged", "y_strainer"),  # longest other-product phrase wins
        ("Pressure Reducing Valve 25 mm", "pressure_reducing_valve"),
        ("Supply of Hydraulic Hose Pipe 1/2 inch 3000 PSI", "hydraulic_hose_pipe"),
        ("Backhoe Loader (V2)", "backhoe_loader"),  # "(V2)" holds a digit
        ("SS Flange 2 inch Class 150", "ss_flange"),
        ("Gland Packing Rope with PTFE coating", "gland_packing_rope"),  # cut before "with"
        ("Mechanical Seal for Centrifugal Pump Model X", "mechanical_seal"),  # "for" is filler
    ],
)
def test_the_key_is_the_titles_product_words(title: str, key: str) -> None:
    assert title_key(title, ONTOLOGIES, OPTIONS) == key


def test_a_title_without_product_words_has_no_key() -> None:
    assert title_key("Supply of 25 Nos", ONTOLOGIES, OPTIONS) is None


def test_the_key_is_found_again_in_its_title() -> None:
    # Matching without an ontology finds a declared type whose words appear in order in the
    # title; the key must be such a run of words.
    from app.matching.chunk_source import declared_types_named

    title = "Supply of Hydraulic Hose Pipe 1/2 inch 3000 PSI"
    key = title_key(title, ONTOLOGIES, OPTIONS)
    assert key is not None
    assert declared_types_named(title, [key]) == {key}
    assert key_phrase(key) == "hydraulic hose pipe"
