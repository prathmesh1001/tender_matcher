from pathlib import Path

import pytest

from app.core.exceptions import OntologyError, UnitConversionError
from app.ontology.loader import load_ontologies, load_ontology_file
from app.ontology.schema import Ontology
from app.ontology.units import class_to_pn, nps_to_dn, pn_to_class, to_canonical

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"


@pytest.fixture(scope="module")
def ontology() -> Ontology:
    return load_ontology_file(ONTOLOGY_DIR / "valves_pumps.yaml")


def test_shipped_ontology_loads(ontology: Ontology) -> None:
    assert ontology.category == "valves_pumps"
    assert ontology.attribute("pressure_rating") is not None
    assert ontology.attribute("nope") is None


def test_numeric_attribute_without_unit_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "category: x\nversion: 1\nproduct_types: [{name: p, synonyms: [p]}]\n"
        "attributes: [{name: head, dimension: length, synonyms: [head]}]\n",
        encoding="utf-8",
    )
    with pytest.raises(OntologyError, match="canonical_unit"):
        load_ontology_file(path)


def test_invalid_regex_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "category: x\nversion: 1\nproduct_types: [{name: p, synonyms: [p]}]\n"
        "attributes: [{name: a, dimension: none, synonyms: [a]}]\n"
        "codes: [{pattern: '(', canonical: x}]\n",
        encoding="utf-8",
    )
    with pytest.raises(OntologyError):
        load_ontology_file(path)


def test_missing_dir_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(OntologyError):
        load_ontologies(tmp_path / "missing")


@pytest.mark.parametrize(
    ("attribute", "value", "unit", "expected", "canonical"),
    [
        ("pressure_rating", 10, "kg/cm²", 9.80665, "bar"),  # B5: kg/cm2 is not bar
        ("pressure_rating", 10, "KG/CM2", 9.80665, "bar"),
        ("pressure_rating", 150, "psig", 10.342, "bar"),
        ("pressure_rating", 1.6, "MPa", 16.0, "bar"),
        ("flow_rate", 16000, "liter/hour", 16.0, "m**3/h"),
        ("power", 2.0, "horsepower", 1.4914, "kW"),
        ("max_temperature", 400, "°F", 204.444, "degC"),
        ("nominal_size", 3, '"', 76.2, "mm"),
    ],
)
def test_to_canonical(
    ontology: Ontology, attribute: str, value: float, unit: str, expected: float, canonical: str
) -> None:
    result = to_canonical(ontology, attribute, value, unit)
    assert result.unit == canonical
    assert result.value == pytest.approx(expected, rel=1e-3)


@pytest.mark.parametrize(
    ("attribute", "unit"),
    [("pressure_rating", "furlongs"), ("pressure_rating", "mm"), ("body_material", "bar")],
)
def test_bad_conversions_raise(ontology: Ontology, attribute: str, unit: str) -> None:
    with pytest.raises(UnitConversionError):
        to_canonical(ontology, attribute, 1, unit)


@pytest.mark.parametrize(("nps", "dn"), [("3", 80), ("1-1/2", 40), ("1.5", 40), ("1/2", 15)])
def test_nps_to_dn(ontology: Ontology, nps: str, dn: int) -> None:
    assert nps_to_dn(ontology, nps) == dn


@pytest.mark.parametrize("nps", ["7", "abc"])
def test_unknown_nps_raises(ontology: Ontology, nps: str) -> None:
    with pytest.raises(UnitConversionError):
        nps_to_dn(ontology, nps)


def test_pn_class_mapping_is_table_only(ontology: Ontology) -> None:
    # B6: Class 150 maps to PN 20 (EN 1759-1), not to PN 16 via a bar value.
    assert class_to_pn(ontology, 150) == 20
    assert pn_to_class(ontology, 20) == 150
    assert pn_to_class(ontology, 16) is None


def test_equivalent_terms_must_not_be_bare_numbers() -> None:
    from pydantic import ValidationError

    from app.ontology.schema import EquivalentGroup

    with pytest.raises(ValidationError, match="bare numbers"):
        EquivalentGroup(name="cast_iron", terms=["cast iron", "IS 210"])  # "210" alone
    assert EquivalentGroup(name="cast_iron", terms=["cast iron", "IS210"]).terms[1] == "IS210"


def test_shipped_ontology_has_material_equivalents(ontology: Ontology) -> None:
    names = {g.name for g in ontology.equivalents}
    assert {"cast_iron", "ductile_iron"} <= names
