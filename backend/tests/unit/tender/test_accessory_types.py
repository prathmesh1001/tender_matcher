"""A type named only after an accessory word is an accessory. Titles are 7865644 BOQ titles."""

from pathlib import Path

import pytest

from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.tender.pipeline import ontology_for, product_type_for, product_types_named

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"


@pytest.fixture(scope="module")
def ontologies() -> list[Ontology]:
    return load_ontologies(ONTOLOGY_DIR)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Thermocouple -K-type- with Thermowell- SS316-446-A182 F316-1.5 Inch300 RF",
            "thermocouple",
        ),
        ("RTD -3-wire- with Thermowell- SS316-446-A182 F316-1.5 Inch300 RF", "rtd"),
        ("Test Thermowell-SS316-446-A182 F316-1.5 Inch300 RF", "thermowell"),
    ],
)
def test_item_is_the_type_named_before_with(
    ontologies: list[Ontology], title: str, expected: str
) -> None:
    assert product_type_for(title, ontology_for(title, ontologies)) == expected


def test_two_types_without_an_accessory_word_stay_unknown(ontologies: list[Ontology]) -> None:
    title = "Thermocouple and RTD set"
    ontology = ontology_for(title, ontologies)
    assert ontology is not None
    assert sorted(product_types_named(title, ontology)) == ["rtd", "thermocouple"]
    assert product_type_for(title, ontology) is None


def test_ontology_without_accessory_words_is_unchanged(ontologies: list[Ontology]) -> None:
    instruments = next(o for o in ontologies if o.category == "instruments")
    plain = instruments.model_copy(update={"accessory_words": []})
    title = "Thermocouple with Thermowell"
    assert sorted(product_types_named(title, plain)) == ["thermocouple", "thermowell"]
